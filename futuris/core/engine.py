"""ForecastEngine: Pipeline orchestration with calibration confidence and drivers."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import numpy as np

from futuris.connectors.base import BaseConnector
from futuris.connectors.synthetic_telemetry import SyntheticTelemetryConnector
from futuris.core.enums import ForecastStatus, SignalClass
from futuris.core.schemas import Driver, Forecast
from futuris.evaluation.confidence import ConfidenceAssessor, confidence_assessor
from futuris.evidence.snapshots import EvidenceSnapshotter
from futuris.features.contextualize import ContextLayer
from futuris.features.drivers import DriverAnalyzer
from futuris.features.normalize import Normalizer
from futuris.infra.logging import get_logger
from futuris.models.base import ModelPrediction
from futuris.models.registry import model_registry
from futuris.connectors.intelx_context import IntelXContextInjector
from futuris.infra.config import settings
from futuris.models.ai_universe_enhanced import AIUniverseModelEnhancer
from futuris.models.routing import ModelRouter, SeriesMetadata

logger = get_logger("futuris.core.engine")


class ForecastEngine:
    """Orchestrates features, model selection, calibration, drivers, and forecast assembly."""

    def __init__(
        self,
        connector: BaseConnector | None = None,
        snapshotter: EvidenceSnapshotter | None = None,
        normalizer: Normalizer | None = None,
        context_layer: ContextLayer | None = None,
        router: ModelRouter | None = None,
        assessor: ConfidenceAssessor | None = None,
        driver_analyzer: DriverAnalyzer | None = None,
        intelx_injector: IntelXContextInjector | None = None,
        enhancer: AIUniverseModelEnhancer | None = None,
    ) -> None:
        self.connector = connector or SyntheticTelemetryConnector(seed=42)
        self.snapshotter = snapshotter or EvidenceSnapshotter()
        self.normalizer = normalizer or Normalizer()
        self.context_layer = context_layer or ContextLayer()
        self.router = router or ModelRouter()
        self.confidence_assessor = assessor or confidence_assessor
        self.driver_analyzer = driver_analyzer or DriverAnalyzer()
        self.intelx_injector = intelx_injector or IntelXContextInjector(
            base_url=settings.INTELX_URL,
            api_key=settings.INTELX_API_KEY,
        )
        self.enhancer = enhancer or AIUniverseModelEnhancer()

    async def orchestrate(
        self,
        target: str,
        as_of: datetime,
        horizon: timedelta,
        capacity_threshold: float = 4000.0,
        history_lookback_days: int = 14,
        evidence_scope: str = "telemetry:synthetic",
        historical_resolved_count: int = 0,
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

        # 4.1 Ingest qualitative exogenous research from IntelX
        intelx_reports = []
        exogenous_adj = {
            "sentiment_multiplier": 1.0,
            "volatility_multiplier": 1.0,
            "confidence_penalty": 0.0,
        }
        try:
            intelx_reports = await self.intelx_injector.fetch_recent_research(target, as_of=as_of)
            exogenous_adj = self.intelx_injector.compute_exogenous_adjustments(intelx_reports)
        except Exception as exc:
            logger.warning("intelx_context_injection_skipped", target=target, error=str(exc))

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

        val_steps = min(horizon_steps, max(12, len(y_series) // 5))
        y_train = y_series.iloc[:-val_steps]
        x_train = x_df.iloc[:-val_steps]
        y_val = y_series.iloc[-val_steps:].to_numpy()

        best_adapter = None
        best_score = float("inf")
        candidate_scores: dict[str, float] = {}
        failed_candidates: dict[str, str] = {}

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
            except Exception as exc:
                failed_candidates[candidate_name] = str(exc)
                logger.warning("candidate_model_fit_failed", candidate=candidate_name, error=str(exc))
                continue

        is_fallback = False
        if best_adapter is None:
            is_fallback = True
            best_adapter = model_registry.get_adapter("naive")
            logger.warning(
                "all_candidate_models_failed_using_fallback",
                candidates=candidates,
                failures=failed_candidates,
            )

        # 7. Refit best adapter on complete historical dataset
        best_adapter.fit(x_df, y_series, as_of=as_of)
        final_prediction: ModelPrediction = best_adapter.predict(
            horizon=horizon_steps,
            capacity_threshold=capacity_threshold,
            probability_method="empirical",
        )

        model_version_str = model_registry.get_version_string(best_adapter)
        if is_fallback:
            model_version_str = f"{model_version_str}:fallback_after_candidate_failures"

        # 7.1 Enhance uncertainty intervals with IntelX qualitative research
        enhanced_ctx = self.enhancer.enhance_forecast(
            base_prediction=final_prediction.central_estimate,
            range_lower=final_prediction.range_lower,
            range_upper=final_prediction.range_upper,
            context_factors=exogenous_adj,
        )

        # 8. Compute Meta-Confidence via ConfidenceAssessor
        confidence_result = self.confidence_assessor.evaluate(
            historical_resolved_count=historical_resolved_count,
            backtest_sample_size=len(y_train),
            long_run_mae=best_score,
            recent_30d_mae=best_score,
            quality_report=signal_set.quality_report,
        )

        # 9. Extract Explanatory Drivers using DriverAnalyzer
        drivers = self.driver_analyzer.analyze_drivers(
            features_df=features_df,
            target_column="value",
            evidence_id=evidence_ref.evidence_id,
        )
        if not drivers:
            mean_val = float(y_series.mean())
            is_pos = final_prediction.central_estimate > mean_val
            drivers = [
                Driver(
                    name="diurnal_traffic_cycle",
                    direction="positive" if is_pos else "neutral",
                    strength=0.85,
                    leading_or_lagging="leading",
                    evidence_refs=[evidence_ref.evidence_id],
                )
            ]

        # 9.1 Inject IntelX Exogenous Research Driver if available
        if intelx_reports and intelx_reports[0].key_findings:
            top_finding = intelx_reports[0].key_findings[0]
            sentiment_positive = exogenous_adj.get("sentiment_multiplier", 1.0) >= 1.0
            drivers.append(
                Driver(
                    name=f"intelx:{top_finding[:28]}",
                    direction="positive" if sentiment_positive else "negative",
                    strength=min(0.95, round(exogenous_adj.get("volatility_multiplier", 1.0) * 0.70, 2)),
                    leading_or_lagging="leading",
                    evidence_refs=[evidence_ref.evidence_id],
                )
            )

        # 10. Assemble Forecast Domain Object
        expires_at = as_of + horizon
        review_at = as_of + (horizon / 4)

        assumptions_list = [
            "traffic regime stable",
            "service architecture unchanged",
            enhanced_ctx.qualitative_nuance,
        ]
        if enhanced_ctx.risk_factors:
            assumptions_list.extend(enhanced_ctx.risk_factors)

        forecast = Forecast(
            forecast_id=forecast_id,
            target=target,
            as_of=as_of,
            horizon=horizon,
            expires_at=expires_at,
            prediction=final_prediction.central_estimate,
            range_lower=enhanced_ctx.adjusted_lower_bound,
            range_upper=enhanced_ctx.adjusted_upper_bound,
            probability=final_prediction.exceedance_probability,
            confidence=confidence_result.level,
            drivers=drivers,
            evidence=[evidence_ref],
            model_version=model_version_str,
            assumptions=assumptions_list,
            review_at=review_at,
            status=ForecastStatus.DRAFT,
            scenario_id=None,
        )

        return [forecast]
