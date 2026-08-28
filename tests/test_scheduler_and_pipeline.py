"""Tests for modular ForecastingPipeline stages, scheduler noise suppression, and jobs."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from futuris.core.enums import ConfidenceLevel, ForecastStatus, SignalClass, SourceTrust
from futuris.core.pipeline import (
    ContextualizationInput,
    ContextualizationStage,
    IngestionInput,
    IngestionStage,
    ModelingInput,
    ModelingStage,
    NormalizationInput,
    NormalizationStage,
)
from futuris.core.schemas import Driver, EvidenceRef, Forecast
from futuris.infra.scheduler import ForecastScheduler


def test_refresh_noise_suppression_logic():
    """Verify scheduler suppresses forecast_updated events for sub-threshold movements."""
    scheduler = ForecastScheduler()
    as_of = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)
    evidence_id = uuid4()

    prev_f = Forecast(
        forecast_id=uuid4(),
        target="service:checkout:capacity_exceedance_24h",
        as_of=as_of,
        horizon=timedelta(hours=24),
        expires_at=as_of + timedelta(hours=24),
        prediction=3500.0,
        range_lower=3000.0,
        range_upper=4000.0,
        probability=0.701,
        confidence=ConfidenceLevel.HIGH,
        drivers=[
            Driver(
                name="traffic",
                direction="positive",
                strength=0.8,
                leading_or_lagging="leading",
                evidence_refs=[evidence_id],
            )
        ],
        evidence=[
            EvidenceRef(
                evidence_id=evidence_id,
                source="telemetry:synthetic",
                source_trust=SourceTrust.HIGH,
                signal_class=SignalClass.TELEMETRY,
                as_of=as_of,
                snapshot_path="/tmp/snap.parquet",
                content_hash="mockhash",
            )
        ],
        model_version="auto_arima@v1",
        assumptions=["stable"],
        review_at=as_of + timedelta(hours=6),
        status=ForecastStatus.ACTIVE,
    )

    # 1. Minor change (0.701 -> 0.702, prediction +5 rpm) -> Must be suppressed
    new_minor = prev_f.model_copy(update={"probability": 0.702, "prediction": 3505.0})
    assert (
        scheduler.should_suppress_refresh_event(
            prev_f, new_minor, delta_prob_threshold=0.05, delta_pred_threshold=50.0
        )
        is True
    )

    # 2. Meaningful probability jump (0.701 -> 0.850) -> Must NOT be suppressed
    new_prob_jump = prev_f.model_copy(update={"probability": 0.850})
    assert (
        scheduler.should_suppress_refresh_event(
            prev_f, new_prob_jump, delta_prob_threshold=0.05, delta_pred_threshold=50.0
        )
        is False
    )

    # 3. Meaningful prediction jump (3500 -> 3700 rpm) -> Must NOT be suppressed
    new_pred_jump = prev_f.model_copy(update={"prediction": 3700.0})
    assert (
        scheduler.should_suppress_refresh_event(
            prev_f, new_pred_jump, delta_prob_threshold=0.05, delta_pred_threshold=50.0
        )
        is False
    )


@pytest.mark.asyncio
async def test_modular_pipeline_stage_composition():
    """Verify each pipeline stage can be instantiated and executed individually in tests."""
    target = "service:checkout:capacity_exceedance_24h"
    as_of = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)
    start_time = as_of - timedelta(days=2)

    # Stage 1: Ingestion
    ingestion = IngestionStage()
    raw = await ingestion.execute(
        IngestionInput(target=target, start_time=start_time, end_time=as_of)
    )
    assert len(raw) > 0

    # Stage 2: Normalization
    normalization = NormalizationStage()
    signal_set = await normalization.execute(NormalizationInput(observations=raw))
    assert signal_set.quality_report.coverage_percentage > 90.0

    # Stage 3: Contextualization
    contextualization = ContextualizationStage()
    features_df, ev_ref = await contextualization.execute(
        ContextualizationInput(signal_set=signal_set, as_of=as_of)
    )
    assert len(features_df) > 0
    assert ev_ref.content_hash is not None

    # Stage 4: Modeling
    modeling = ModelingStage()
    modeling_out = await modeling.execute(
        ModelingInput(
            features_df=features_df,
            signal_set=signal_set,
            target=target,
            as_of=as_of,
            horizon=timedelta(hours=24),
            capacity_threshold=4000.0,
        ),
        evidence_ref=ev_ref,
    )
    assert modeling_out.prediction.central_estimate > 0.0


@pytest.mark.asyncio
async def test_scheduler_jobs_direct_trigger():
    """Verify scheduler job functions can be triggered directly without real-time sleep loops."""
    scheduler = ForecastScheduler()

    # 1. Trigger Ingestion Job
    points = await scheduler.ingest_job()
    assert points > 0

    # 2. Trigger Forecast Refresh Job
    refreshed = await scheduler.forecast_refresh_job()
    assert len(refreshed) == 1
    assert refreshed[0].target == "service:checkout:capacity_exceedance_24h"
