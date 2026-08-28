"""ForecastEngine: End-to-end forecasting pipeline orchestration."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import numpy as np

from futuris.connectors.base import BaseConnector
from futuris.connectors.synthetic_telemetry import SyntheticTelemetryConnector
from futuris.core.enums import ConfidenceLevel, ForecastStatus, SignalClass
from futuris.core.schemas import Driver, Forecast
from futuris.evidence.snapshots import EvidenceSnapshotter
from futuris.features.contextualize import ContextLayer
from futuris.features.normalize import Normalizer
from futuris.models.base import ModelPrediction
from futuris.models.registry import model_registry
from futuris.models.routing import ModelRouter, SeriesMetadata


class ForecastEngine:
    """Orchestrates ingestion, normalization, features, model selection, and forecast assembly."""

    def __init__(
        self,
        connector: BaseConnector | None = None,
        snapshotter: EvidenceSnapshotter | None = None,
        normalizer: Normalizer | None = None,
        context_layer: ContextLayer | None = None,
        router: ModelRouter | None = None,
    ) -> None:
        self.connector = connector or SyntheticTelemetryConnector(seed=42)
        self.snapshotter = snapshotter or EvidenceSnapshotter()
        self.normalizer = normalizer or Normalizer()
        self.context_layer = context_layer or ContextLayer()
        self.router = router or ModelRouter()

    async def orchestrate(
        self,
        target: str,
        as_of: datetime,
        horizon: timedelta,
        capacity_threshold: float = 4000.0,
        history_lookback_days: int = 14,
        evidence_scope: str = "telemetry:synthetic",
    ) -> list[Forecast]:
        """Produce a draft Forecast object for the target with zero future-data leakage."""
        as_of = as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of.astimezone(UTC)

        start_time = as_of - timedelta(days=history_lookback_days)

        # 1. Ingest raw observations
        raw_observations = await self.connector.fetch(start_time, as_of)

        # 2. Normalize and align grid
        signal_set = self.normalizer.normalize(raw_observations)

        # 3. Contextualize features (strictly up to as_of)
        features_df = self.context_layer.build_feature_table(signal_set, as_of=as_of)

        # 4. Freeze immutable point-in-time snapshot
        forecast_id = uuid4()
        evidence_ref = self.snapshotter.freeze_snapshot(
            signal_set=signal_set,
            as_of=as_of,
            forecast_id=forecast_id,
            source_id=evidence_scope,
            signal_class=SignalClass.TELEMETRY,
        )

        # 5. Route candidate models
        step_minutes = signal_set.grid_step_minutes
        horizon_steps = max(1, int(horizon.total_seconds() // (step_minutes * 60)))
        metadata = SeriesMetadata(
            history_points=len(features_df),
            frequency_minutes=step_minutes,
            has_weekly_seasonality=len(features_df) >= 2016,
        )
        candidates = self.router.select_candidates(
            metadata, horizon_steps, signal_set.quality_report
        )

        # 6. Held-out backtest selection over candidates
        y_series = features_df["value"]
        x_df = features_df.drop(columns=["value"])

        # Train/validation split on trailing window
        val_steps = min(horizon_steps, max(12, len(y_series) // 5))
        y_train = y_series.iloc[:-val_steps]
        x_train = x_df.iloc[:-val_steps]
        y_val = y_series.iloc[-val_steps:].to_numpy()

        best_adapter = None
        best_score = float("inf")
        candidate_scores: dict[str, float] = {}

        for candidate_name in candidates:
            adapter = model_registry.get_adapter(candidate_name)
            try:
                split_time = as_of - timedelta(minutes=val_steps * step_minutes)
                adapter.fit(x_train, y_train, as_of=split_time)
                val_pred = adapter.predict(val_steps)
                mae = float(np.mean(np.abs(np.array(val_pred.point_forecast) - y_val)))
                candidate_scores[candidate_name] = mae
                if mae < best_score:
                    best_score = mae
                    best_adapter = adapter
            except Exception:
                continue

        if best_adapter is None:
            best_adapter = model_registry.get_adapter("naive")

        # 7. Refit best adapter on complete historical dataset
        best_adapter.fit(x_df, y_series, as_of=as_of)
        final_prediction: ModelPrediction = best_adapter.predict(
            horizon=horizon_steps,
            capacity_threshold=capacity_threshold,
            probability_method="empirical",
        )

        model_version_str = model_registry.get_version_string(best_adapter)

        # 8. Construct Explanatory Drivers
        y_mean = float(y_series.mean())
        y_std = float(y_series.std())
        strength_score = round(
            float(abs(final_prediction.central_estimate - y_mean) / (y_std + 1e-5)), 2
        )
        drivers = [
            Driver(
                name="diurnal_traffic_cycle",
                direction="positive" if final_prediction.central_estimate > y_mean else "neutral",
                strength=strength_score,
                leading_or_lagging="leading",
                evidence_refs=[evidence_ref.evidence_id],
            )
        ]

        # 9. Assemble Forecast Domain Object
        expires_at = as_of + horizon
        review_at = as_of + (horizon / 4)

        forecast = Forecast(
            forecast_id=forecast_id,
            target=target,
            as_of=as_of,
            horizon=horizon,
            expires_at=expires_at,
            prediction=final_prediction.central_estimate,
            range_lower=final_prediction.range_lower,
            range_upper=final_prediction.range_upper,
            probability=final_prediction.exceedance_probability,
            confidence=ConfidenceLevel.LOW,
            drivers=drivers,
            evidence=[evidence_ref],
            model_version=model_version_str,
            assumptions=["traffic regime stable", "service architecture unchanged"],
            review_at=review_at,
            status=ForecastStatus.DRAFT,
            scenario_id=None,
        )

        return [forecast]
