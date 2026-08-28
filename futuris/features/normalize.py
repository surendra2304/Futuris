"""Data normalization, deduplication, regular grid alignment, and quality reporting."""

from datetime import UTC, datetime
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from futuris.connectors.base import Observation


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
            msg = "Cannot normalize empty observation list."
            raise ValueError(msg)

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
                raise ValueError(msg)

            dt = obs.observed_at
            dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)

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
