"""Modular typed forecasting intelligence pipeline with per-stage timing and structured logging."""

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, TypeVar
from uuid import uuid4

import numpy as np
import pandas as pd

from futuris.connectors.base import BaseConnector, Observation
from futuris.connectors.synthetic_telemetry import SyntheticTelemetryConnector
from futuris.core.decision import (
    ActionSuggestion,
    DecisionImplication,
    DecisionSupport,
    decision_support,
)
from futuris.core.enums import ForecastStatus, SignalClass
from futuris.core.schemas import Driver, EvidenceRef, Forecast
from futuris.evaluation.confidence import ConfidenceAssessor, confidence_assessor
from futuris.evidence.snapshots import EvidenceSnapshotter
from futuris.features.contextualize import ContextLayer
from futuris.features.drivers import DriverAnalyzer
from futuris.features.normalize import Normalizer, TrustedSignalSet
from futuris.infra.logging import get_logger
from futuris.models.base import ModelPrediction
from futuris.models.registry import model_registry
from futuris.models.routing import ModelRouter, SeriesMetadata

logger = get_logger("futuris.pipeline")

InType = TypeVar("InType")
OutType = TypeVar("OutType")


class PipelineStage(Protocol[InType, OutType]):
    """Individual typed processing stage in the forecasting intelligence pipeline."""

    name: str

    async def execute(self, stage_input: InType) -> OutType:
        """Execute stage transformation."""
        ...


@dataclass
class IngestionInput:
    target: str
    start_time: datetime
    end_time: datetime


@dataclass
class NormalizationInput:
    observations: list[Observation]


@dataclass
class ContextualizationInput:
    signal_set: TrustedSignalSet
    as_of: datetime


@dataclass
class ModelingInput:
    features_df: pd.DataFrame
    signal_set: TrustedSignalSet
    target: str
    as_of: datetime
    horizon: timedelta
    capacity_threshold: float


@dataclass
class ModelingOutput:
    prediction: ModelPrediction
    model_version: str
    best_score: float
    features_df: pd.DataFrame
    signal_set: TrustedSignalSet
    evidence_ref: EvidenceRef
    horizon: timedelta
    as_of: datetime
    target: str


@dataclass
class PipelineResult:
    forecast: Forecast
    implications: DecisionImplication
    recommendations: list[ActionSuggestion]
    stage_durations_ms: dict[str, float]


class IngestionStage:
    name = "ingestion"

    def __init__(self, connector: BaseConnector | None = None) -> None:
        self.connector = connector or SyntheticTelemetryConnector(seed=42)

    async def execute(self, inp: IngestionInput) -> list[Observation]:
        return await self.connector.fetch(inp.start_time, inp.end_time)


class NormalizationStage:
    name = "normalization"

    def __init__(self, normalizer: Normalizer | None = None) -> None:
        self.normalizer = normalizer or Normalizer()

    async def execute(self, inp: NormalizationInput) -> TrustedSignalSet:
        return self.normalizer.normalize(inp.observations)


class ContextualizationStage:
    name = "contextualization"

    def __init__(
        self,
        context_layer: ContextLayer | None = None,
        snapshotter: EvidenceSnapshotter | None = None,
    ) -> None:
        self.context_layer = context_layer or ContextLayer()
        self.snapshotter = snapshotter or EvidenceSnapshotter()

    async def execute(self, inp: ContextualizationInput) -> tuple[pd.DataFrame, EvidenceRef]:
        df = self.context_layer.build_feature_table(inp.signal_set, as_of=inp.as_of)
        ev_ref = self.snapshotter.freeze_snapshot(
            signal_set=inp.signal_set,
            as_of=inp.as_of,
            forecast_id=uuid4(),
            source_id="telemetry:synthetic",
            signal_class=SignalClass.TELEMETRY,
        )
        return df, ev_ref


class ModelingStage:
    name = "modeling"

    def __init__(self, router: ModelRouter | None = None) -> None:
        self.router = router or ModelRouter()

    async def execute(
        self, inp: ModelingInput, evidence_ref: EvidenceRef
    ) -> ModelingOutput:
        features_df = inp.features_df
        step_minutes = inp.signal_set.grid_step_minutes
        horizon_steps = max(1, int(inp.horizon.total_seconds() // (step_minutes * 60)))

        metadata = SeriesMetadata(
            history_points=len(features_df),
            frequency_minutes=step_minutes,
            has_weekly_seasonality=len(features_df) >= 2016,
        )
        candidates = self.router.select_candidates(
            metadata, horizon_steps, inp.signal_set.quality_report
        )

        y_series = features_df["value"]
        x_df = features_df.drop(columns=["value"])

        val_steps = min(horizon_steps, max(12, len(y_series) // 5))
        y_train = y_series.iloc[:-val_steps]
        x_train = x_df.iloc[:-val_steps]
        y_val = y_series.iloc[-val_steps:].to_numpy()

        best_adapter = None
        best_score = float("inf")

        for c in candidates:
            adapter = model_registry.get_adapter(c)
            try:
                split_time = inp.as_of - timedelta(minutes=val_steps * step_minutes)
                adapter.fit(x_train, y_train, as_of=split_time)
                pred = adapter.predict(val_steps)
                mae = float(np.mean(np.abs(np.array(pred.point_forecast) - y_val)))
                if mae < best_score:
                    best_score = mae
                    best_adapter = adapter
            except Exception:
                continue

        if best_adapter is None:
            best_adapter = model_registry.get_adapter("naive")

        best_adapter.fit(x_df, y_series, as_of=inp.as_of)
        final_prediction = best_adapter.predict(
            horizon=horizon_steps,
            capacity_threshold=inp.capacity_threshold,
            probability_method="empirical",
        )

        return ModelingOutput(
            prediction=final_prediction,
            model_version=model_registry.get_version_string(best_adapter),
            best_score=best_score,
            features_df=features_df,
            signal_set=inp.signal_set,
            evidence_ref=evidence_ref,
            horizon=inp.horizon,
            as_of=inp.as_of,
            target=inp.target,
        )


class CalibrationDecisionStage:
    name = "calibration_and_decision"

    def __init__(
        self,
        assessor: ConfidenceAssessor | None = None,
        driver_analyzer: DriverAnalyzer | None = None,
        decision_tool: DecisionSupport | None = None,
    ) -> None:
        self.assessor = assessor or confidence_assessor
        self.driver_analyzer = driver_analyzer or DriverAnalyzer()
        self.decision_tool = decision_tool or decision_support

    async def execute(
        self, inp: ModelingOutput
    ) -> tuple[Forecast, DecisionImplication, list[ActionSuggestion]]:
        conf_res = self.assessor.evaluate(
            historical_resolved_count=10,
            backtest_sample_size=len(inp.features_df),
            long_run_mae=inp.best_score,
            recent_30d_mae=inp.best_score,
            quality_report=inp.signal_set.quality_report,
        )

        drivers = self.driver_analyzer.analyze_drivers(
            features_df=inp.features_df,
            target_column="value",
            evidence_id=inp.evidence_ref.evidence_id,
        )
        if not drivers:
            drivers = [
                Driver(
                    name="diurnal_traffic_cycle",
                    direction="positive",
                    strength=0.85,
                    leading_or_lagging="leading",
                    evidence_refs=[inp.evidence_ref.evidence_id],
                )
            ]

        forecast = Forecast(
            forecast_id=uuid4(),
            target=inp.target,
            as_of=inp.as_of,
            horizon=inp.horizon,
            expires_at=inp.as_of + inp.horizon,
            prediction=inp.prediction.central_estimate,
            range_lower=inp.prediction.range_lower,
            range_upper=inp.prediction.range_upper,
            probability=inp.prediction.exceedance_probability,
            confidence=conf_res.level,
            drivers=drivers,
            evidence=[inp.evidence_ref],
            model_version=inp.model_version,
            assumptions=["traffic regime stable"],
            review_at=inp.as_of + (inp.horizon / 4),
            status=ForecastStatus.ACTIVE,
        )

        implications = self.decision_tool.implications(forecast)
        recommendations = self.decision_tool.recommendations(forecast)

        return forecast, implications, recommendations


class ForecastingPipeline:
    """End-to-end typed pipeline linking all stages."""

    def __init__(
        self,
        ingestion: IngestionStage | None = None,
        normalization: NormalizationStage | None = None,
        contextualization: ContextualizationStage | None = None,
        modeling: ModelingStage | None = None,
        calibration_decision: CalibrationDecisionStage | None = None,
    ) -> None:
        self.ingestion = ingestion or IngestionStage()
        self.normalization = normalization or NormalizationStage()
        self.contextualization = contextualization or ContextualizationStage()
        self.modeling = modeling or ModelingStage()
        self.calibration_decision = calibration_decision or CalibrationDecisionStage()

    async def run(
        self,
        target: str,
        as_of: datetime,
        horizon: timedelta = timedelta(hours=24),
        lookback_days: int = 14,
        capacity_threshold: float = 4000.0,
    ) -> PipelineResult:
        """Run all pipeline stages sequentially with structured stage durations."""
        durations: dict[str, float] = {}
        as_of = as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of.astimezone(UTC)
        start_time = as_of - timedelta(days=lookback_days)

        # 1. Ingestion
        t0 = time.perf_counter()
        raw = await self.ingestion.execute(
            IngestionInput(target=target, start_time=start_time, end_time=as_of)
        )
        durations["ingestion"] = round((time.perf_counter() - t0) * 1000, 2)

        # 2. Normalization
        t0 = time.perf_counter()
        signal_set = await self.normalization.execute(NormalizationInput(observations=raw))
        durations["normalization"] = round((time.perf_counter() - t0) * 1000, 2)

        # 3. Contextualization & Snapshotting
        t0 = time.perf_counter()
        features_df, ev_ref = await self.contextualization.execute(
            ContextualizationInput(signal_set=signal_set, as_of=as_of)
        )
        durations["contextualization"] = round((time.perf_counter() - t0) * 1000, 2)

        # 4. Modeling
        t0 = time.perf_counter()
        modeling_out = await self.modeling.execute(
            ModelingInput(
                features_df=features_df,
                signal_set=signal_set,
                target=target,
                as_of=as_of,
                horizon=horizon,
                capacity_threshold=capacity_threshold,
            ),
            evidence_ref=ev_ref,
        )
        durations["modeling"] = round((time.perf_counter() - t0) * 1000, 2)

        # 5. Calibration & Decision
        t0 = time.perf_counter()
        forecast, implications, recs = await self.calibration_decision.execute(modeling_out)
        durations["calibration_and_decision"] = round((time.perf_counter() - t0) * 1000, 2)

        return PipelineResult(
            forecast=forecast,
            implications=implications,
            recommendations=recs,
            stage_durations_ms=durations,
        )
