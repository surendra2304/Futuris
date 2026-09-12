"""Data normalization, deduplication, regular grid alignment, and quality reporting."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from futuris.connectors.base import Observation


class TimezoneNormalizationError(ValueError):
    """Raised when an invalid, ambiguous, or unparseable timezone is encountered."""
    pass


class UnitMismatchError(ValueError):
    """Raised when incoming telemetry observations have mismatched measurement units."""
    pass


class HorizonMismatchError(ValueError):
    """Raised when a requested horizon mismatches the temporal span or resolution."""
    pass


class DataStalenessError(RuntimeError):
    """Raised when telemetry data is stale and exceeds the maximum acceptable latency."""
    pass


class InsufficientDataError(RuntimeError):
    """Raised when observation data is too sparse, truncated, or low-coverage for forecasting."""
    pass


def normalize_timestamp(dt: Any) -> datetime:
    """Normalize any datetime, ISO-8601 string, or timestamp into an explicit UTC datetime."""
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    if isinstance(dt, str):
        try:
            parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        except Exception as err:
            raise TimezoneNormalizationError(f"Cannot normalize timestamp string '{dt}' to UTC: {err}") from err
    if isinstance(dt, (int, float)):
        try:
            return datetime.fromtimestamp(dt, tz=UTC)
        except Exception as err:
            raise TimezoneNormalizationError(f"Cannot normalize epoch timestamp '{dt}' to UTC: {err}") from err
    raise TimezoneNormalizationError(f"Unsupported timestamp type: {type(dt)}")


def _extract_timestamp(item: Any) -> datetime:
    """Helper to extract datetime from Observation, dict, datetime, or string."""
    if hasattr(item, "observed_at"):
        return normalize_timestamp(item.observed_at)
    if hasattr(item, "timestamp"):
        return normalize_timestamp(item.timestamp)
    if isinstance(item, dict):
        raw = item.get("observed_at") or item.get("timestamp") or item.get("time") or item.get("as_of")
        if raw is not None:
            return normalize_timestamp(raw)
    return normalize_timestamp(item)


def check_data_staleness(
    observations: Any,
    as_of: datetime | None = None,
    max_staleness_hours: float | None = None,
    max_staleness_seconds: float | None = None,
    raise_error: bool = False,
) -> bool:
    """Evaluate whether the latest observation timestamp is stale compared to as_of."""
    if not observations:
        if raise_error:
            raise DataStalenessError("Cannot check staleness: observations dataset is empty.")
        return True

    ref_as_of = normalize_timestamp(as_of) if as_of is not None else datetime.now(UTC)
    threshold_seconds = (
        max_staleness_seconds
        if max_staleness_seconds is not None
        else (max_staleness_hours if max_staleness_hours is not None else 24.0) * 3600.0
    )

    try:
        latest_obs = max(_extract_timestamp(o) for o in observations)
    except Exception as err:
        if raise_error:
            raise DataStalenessError(f"Failed to parse observation timestamps: {err}") from err
        return True

    time_delta = (ref_as_of - latest_obs).total_seconds()
    is_stale = time_delta > threshold_seconds

    if is_stale and raise_error:
        raise DataStalenessError(
            f"Data staleness detected: latest observation ({latest_obs.isoformat()}) is "
            f"{time_delta:.1f}s old (exceeds threshold of {threshold_seconds:.1f}s)."
        )
    return is_stale


def check_insufficient_data(
    observations: Any,
    min_points: int = 24,
    min_coverage_percentage: float = 50.0,
    raise_error: bool = False,
) -> tuple[bool, str]:
    """Check if observation set meets minimum volume and coverage thresholds."""
    count = len(observations) if hasattr(observations, "__len__") else 0
    if count == 0:
        msg = "Insufficient data: zero observations provided."
        if raise_error:
            raise InsufficientDataError(msg)
        return True, msg

    if count < min_points:
        msg = f"Insufficient data: observation count ({count}) is below minimum required ({min_points})."
        if raise_error:
            raise InsufficientDataError(msg)
        return True, msg

    return False, "Data sufficiency verified."


def validate_horizon(
    start_time: Any,
    end_time: Any,
    min_horizon: timedelta = timedelta(minutes=5),
    max_horizon: timedelta = timedelta(days=90),
) -> timedelta:
    """Validate that end_time > start_time and horizon duration is within acceptable bounds."""
    start_utc = normalize_timestamp(start_time)
    end_utc = normalize_timestamp(end_time)
    if end_utc <= start_utc:
        raise HorizonMismatchError(
            f"Horizon end time ({end_utc.isoformat()}) must be strictly after start time ({start_utc.isoformat()})."
        )
    delta = end_utc - start_utc
    if delta < min_horizon:
        raise HorizonMismatchError(
            f"Horizon duration {delta} is below minimum allowed horizon {min_horizon}."
        )
    if delta > max_horizon:
        raise HorizonMismatchError(
            f"Horizon duration {delta} exceeds maximum allowed horizon {max_horizon}."
        )
    return delta


def check_missing_telemetry(
    timestamps: list[Any],
    max_allowed_gap: timedelta = timedelta(hours=2),
) -> bool:
    """Check for telemetry dropouts or temporal gaps exceeding the maximum allowed interval."""
    if len(timestamps) < 2:
        return False
    normalized = sorted(normalize_timestamp(t) for t in timestamps)
    for i in range(1, len(normalized)):
        gap = normalized[i] - normalized[i - 1]
        if gap > max_allowed_gap:
            raise InsufficientDataError(
                f"Missing telemetry gap of {gap} detected between {normalized[i-1].isoformat()} "
                f"and {normalized[i].isoformat()} (max allowed gap: {max_allowed_gap})."
            )
    return False


class DataQualityReport(BaseModel):
    """Quality metrics for ingested and aligned signal time-series."""

    model_config = ConfigDict(extra="forbid")

    total_raw_points: int
    cleaned_points: int
    duplicates_dropped: int
    gaps_filled_under_15m: int
    long_gaps_count: int
    anomalies_clipped: int
    coverage_percentage: float


class TrustedSignalSet(BaseModel):
    """Normalized, deduplicated, and regularized time-series dataset."""

    model_config = ConfigDict(extra="forbid")

    series_id: str
    unit: str
    grid_step_minutes: int
    start_time: datetime
    end_time: datetime
    timestamps: list[datetime]
    values: list[float]
    quality_report: DataQualityReport
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dataframe(self) -> pd.DataFrame:
        """Convert trusted signal set into a indexed Pandas DataFrame."""
        df = pd.DataFrame({"timestamp": self.timestamps, "value": self.values})
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df.set_index("timestamp", inplace=True)
        return df


class Normalizer:
    """Normalizes raw observations onto a deterministic time grid with quality checks."""

    def __init__(
        self,
        grid_step_minutes: int = 5,
        max_fill_gap_minutes: int = 15,
        min_valid_value: float = 0.0,
        max_valid_value: float = 1_000_000.0,
    ) -> None:
        self.grid_step_minutes = grid_step_minutes
        self.max_fill_gap_minutes = max_fill_gap_minutes
        self.min_valid_value = min_valid_value
        self.max_valid_value = max_valid_value

    def normalize(
        self,
        observations: list[Observation],
        expected_series_id: str | None = None,
        expected_unit: str | None = None,
    ) -> TrustedSignalSet:
        """Clean, deduplicate, align to grid, and compute data quality report."""
        if not observations:
            raise InsufficientDataError("Cannot normalize empty observation list.")

        total_raw = len(observations)
        series_id = expected_series_id or observations[0].series_id
        unit = expected_unit or observations[0].unit

        # 1. Filter by matching series_id and unit, enforce UTC timestamps
        valid_records: list[dict[str, Any]] = []
        for obs in observations:
            if expected_series_id and obs.series_id != expected_series_id:
                continue
            if expected_unit and obs.unit != expected_unit:
                msg = f"Unit mismatch: expected {expected_unit}, got {obs.unit}"
                raise UnitMismatchError(msg)

            dt = normalize_timestamp(obs.observed_at)

            valid_records.append({
                "timestamp": dt,
                "value": float(obs.value),
            })

        if not valid_records:
            msg = f"No valid records matching series_id '{series_id}'"
            raise ValueError(msg)

        df = pd.DataFrame(valid_records)

        # 2. Clean: Drop nulls, clip impossible values
        df.dropna(subset=["timestamp", "value"], inplace=True)
        anomalies_clipped = int(
            ((df["value"] < self.min_valid_value) | (df["value"] > self.max_valid_value)).sum()
        )
        df["value"] = df["value"].clip(lower=self.min_valid_value, upper=self.max_valid_value)

        # 3. Deduplicate: Last-write-wins on identical timestamps
        before_dedup = len(df)
        df.drop_duplicates(subset=["timestamp"], keep="last", inplace=True)
        duplicates_dropped = before_dedup - len(df)

        df.sort_values("timestamp", inplace=True)

        # 4. Align to regular grid
        start_time = df["timestamp"].min().floor(f"{self.grid_step_minutes}min")
        end_time = df["timestamp"].max().ceil(f"{self.grid_step_minutes}min")

        grid_freq = f"{self.grid_step_minutes}min"
        full_grid = pd.date_range(start=start_time, end=end_time, freq=grid_freq, tz=UTC)
        total_expected_points = len(full_grid)

        # Resample onto grid with mean aggregation for close observations
        df.set_index("timestamp", inplace=True)
        resampled = df.resample(grid_freq).mean()
        resampled = resampled.reindex(full_grid)

        # Gap interpolation policy: forward-fill <= max_fill_gap_minutes
        max_fill_limit = self.max_fill_gap_minutes // self.grid_step_minutes
        pre_fill_nulls = int(resampled["value"].isna().sum())

        filled = resampled["value"].ffill(limit=max_fill_limit)
        post_fill_nulls = int(filled.isna().sum())
        gaps_filled = pre_fill_nulls - post_fill_nulls

        # Fallback backfill for initial missing point if any
        filled = filled.bfill(limit=1).fillna(0.0)

        # Count long gaps
        long_gaps_count = post_fill_nulls
        cleaned_points = len(filled)
        coverage_pct = round(
            (1.0 - (long_gaps_count / max(1, total_expected_points))) * 100.0, 2
        )

        quality_report = DataQualityReport(
            total_raw_points=total_raw,
            cleaned_points=cleaned_points,
            duplicates_dropped=duplicates_dropped,
            gaps_filled_under_15m=gaps_filled,
            long_gaps_count=long_gaps_count,
            anomalies_clipped=anomalies_clipped,
            coverage_percentage=coverage_pct,
        )

        return TrustedSignalSet(
            series_id=series_id,
            unit=unit,
            grid_step_minutes=self.grid_step_minutes,
            start_time=start_time.to_pydatetime(),
            end_time=end_time.to_pydatetime(),
            timestamps=[ts.to_pydatetime() for ts in filled.index],
            values=[float(v) for v in filled.values],
            quality_report=quality_report,
            metadata={"service": series_id.split(":")[0]},
        )
