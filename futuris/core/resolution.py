"""Outcome resolution rules and snapshot-anchored ground truth verification."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import pandas as pd

from futuris.core.enums import ResolutionMethod
from futuris.core.schemas import Forecast, Outcome


@dataclass(frozen=True)
class ResolutionRuleMeta:
    """Metadata describing a specific versioned resolution rule."""

    rule_id: str
    version: str
    target_pattern: str
    description: str


class ResolutionRule(Protocol):
    """Protocol for versioned target resolution logic."""

    @property
    def meta(self) -> ResolutionRuleMeta:
        ...

    def resolve(
        self,
        forecast: Forecast,
        observation_records: pd.DataFrame,
        evidence_snapshot_data: pd.DataFrame | None = None,
    ) -> Outcome:
        ...


class CapacityExceedanceResolutionRuleV1:
    """Rule v1 for capacity exceedance targets:

    capacity_exceedance_24h resolves TRUE iff observed max demand in the window
    [as_of, expires_at] exceeds the capacity threshold; observed max demand is the outcome value.
    If observations have > 20% data gaps, marks resolution_method=ambiguous.
    """

    meta = ResolutionRuleMeta(
        rule_id="rule:capacity_exceedance",
        version="v1.0",
        target_pattern="service:*:capacity_exceedance_*",
        description="Resolves TRUE if observed demand >= capacity threshold in window.",
    )

    def resolve(
        self,
        forecast: Forecast,
        observation_records: pd.DataFrame,
        evidence_snapshot_data: pd.DataFrame | None = None,
        max_allowed_gap_pct: float = 20.0,
    ) -> Outcome:
        as_of = forecast.as_of
        expires_at = forecast.expires_at

        # Ensure datetime index in UTC
        df = observation_records.copy()
        if not isinstance(df.index, pd.DatetimeIndex):
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                df.set_index("timestamp", inplace=True)
            elif "observed_at" in df.columns:
                df["observed_at"] = pd.to_datetime(df["observed_at"], utc=True)
                df.set_index("observed_at", inplace=True)

        # Slice exact window [as_of, expires_at]
        window_df = df[(df.index > as_of) & (df.index <= expires_at)]

        # Calculate expected number of 5m steps
        total_seconds = (expires_at - as_of).total_seconds()
        expected_points = max(1, int(total_seconds // 300))
        actual_points = len(window_df)

        gap_pct = max(0.0, (1.0 - (actual_points / expected_points)) * 100.0)

        # 1. Ambiguity handling for missing data
        if gap_pct > max_allowed_gap_pct or window_df.empty:
            msg = (
                f"Excessive observation gaps ({gap_pct:.1f}% > {max_allowed_gap_pct}%) "
                "in evaluation window"
            )
            return Outcome(
                outcome_id=uuid4(),
                forecast_id=forecast.forecast_id,
                observed_value=float(window_df["value"].max()) if not window_df.empty else None,
                event_occurred=None,
                resolved_at=datetime.now(UTC),
                resolution_method=ResolutionMethod.AMBIGUOUS,
                ambiguity_note=msg,
                resolution_rule_version=f"{self.meta.rule_id}:{self.meta.version}",
            )

        # 2. Extract capacity threshold from assumptions or default to 4000.0
        capacity_threshold = 4000.0
        if (
            evidence_snapshot_data is not None
            and "capacity_limit" in evidence_snapshot_data.columns
        ):
            capacity_threshold = float(evidence_snapshot_data["capacity_limit"].iloc[-1])

        max_observed = float(window_df["value"].max())
        event_occurred = bool(max_observed >= capacity_threshold)

        return Outcome(
            outcome_id=uuid4(),
            forecast_id=forecast.forecast_id,
            observed_value=round(max_observed, 2),
            event_occurred=event_occurred,
            resolved_at=datetime.now(UTC),
            resolution_method=ResolutionMethod.AUTOMATIC,
            ambiguity_note=None,
            resolution_rule_version=f"{self.meta.rule_id}:{self.meta.version}",
        )


class OutcomeResolver:
    """Registry and executor for versioned target resolution rules."""

    def __init__(self) -> None:
        self.rules: dict[str, ResolutionRule] = {
            "capacity_exceedance": CapacityExceedanceResolutionRuleV1(),
        }

    def resolve_forecast(
        self,
        forecast: Forecast,
        observations: pd.DataFrame,
    ) -> Outcome:
        """Resolve a forecast against observation data and evidence snapshots."""
        # Load evidence snapshot if present
        evidence_df = None
        if forecast.evidence:
            snap_path = Path(forecast.evidence[0].snapshot_path)
            if snap_path.exists():
                try:
                    evidence_df = pd.read_parquet(snap_path)
                except Exception:
                    evidence_df = None

        rule = self.rules.get("capacity_exceedance")
        if not rule:
            msg = "No resolution rule found for target."
            raise ValueError(msg)

        return rule.resolve(
            forecast=forecast,
            observation_records=observations,
            evidence_snapshot_data=evidence_df,
        )


outcome_resolver = OutcomeResolver()
