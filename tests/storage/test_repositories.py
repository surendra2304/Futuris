"""Comprehensive integration tests for storage repositories and point-in-time state."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from futuris.core import (
    ConfidenceLevel,
    Driver,
    EvidenceRef,
    Forecast,
    ForecastEvent,
    ForecastEventType,
    ForecastStatus,
    ModelInfo,
    Outcome,
    ResolutionMethod,
    SignalClass,
    SourceTrust,
)
from futuris.storage.models import Base
from futuris.storage.repositories import (
    EvaluationRepository,
    EventRepository,
    ForecastRepository,
    ModelPromotionError,
    ModelRepository,
    OutcomeRepository,
    ReadOnlyAuditViolationError,
)

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")


@pytest_asyncio.fixture
async def test_session() -> AsyncSession:
    """Provide an isolated test database session and create schema."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def sample_evidence() -> EvidenceRef:
    return EvidenceRef(
        evidence_id=uuid4(),
        source="telemetry:datadog:checkout_service",
        source_trust=SourceTrust.HIGH,
        signal_class=SignalClass.TELEMETRY,
        as_of=datetime.now(UTC),
        snapshot_path="/data/storage/evidence/snap_1.parquet",
        content_hash="sha256_mock_hash_for_test",
    )


@pytest.fixture
def sample_forecast(sample_evidence: EvidenceRef) -> Forecast:
    as_of = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)
    return Forecast(
        forecast_id=uuid4(),
        target="service:checkout:error_rate_24h",
        as_of=as_of,
        horizon=timedelta(hours=24),
        expires_at=as_of + timedelta(hours=24),
        prediction=2.45,
        range_lower=1.20,
        range_upper=4.50,
        probability=0.35,
        confidence=ConfidenceLevel.HIGH,
        drivers=[
            Driver(
                name="traffic_surge",
                direction="positive",
                strength=0.85,
                leading_or_lagging="leading",
                evidence_refs=[sample_evidence.evidence_id],
            )
        ],
        evidence=[sample_evidence],
        model_version="auto_arima:v1.0.0",
        assumptions=["stable downstream dependencies"],
        review_at=as_of + timedelta(hours=4),
        status=ForecastStatus.ACTIVE,
    )


@pytest.mark.asyncio
async def test_forecast_create_and_get_round_trip(
    test_session: AsyncSession, sample_forecast: Forecast
):
    """Test saving and retrieving forecast aggregate."""
    repo = ForecastRepository(test_session)
    created = await repo.create(sample_forecast)
    assert created.forecast_id == sample_forecast.forecast_id

    fetched = await repo.get(sample_forecast.forecast_id)
    assert fetched is not None
    assert fetched.target == sample_forecast.target
    assert fetched.prediction == 2.45
    assert fetched.confidence == ConfidenceLevel.HIGH
    assert len(fetched.evidence) == 1
    assert fetched.evidence[0].evidence_id == sample_forecast.evidence[0].evidence_id
    assert len(fetched.drivers) == 1
    assert fetched.drivers[0].name == "traffic_surge"


@pytest.mark.asyncio
async def test_forecast_point_in_time_query_correctness(
    test_session: AsyncSession, sample_forecast: Forecast
):
    """Verify point_in_time_query returns original state without leaking subsequent updates."""
    repo = ForecastRepository(test_session)
    t0 = datetime(2026, 8, 28, 10, 0, 0, tzinfo=UTC)
    sample_forecast.as_of = t0
    sample_forecast.expires_at = t0 + timedelta(hours=24)
    sample_forecast.review_at = t0 + timedelta(hours=6)

    # 1. Create original forecast
    await repo.create(sample_forecast)

    # 2. Update status at a later time
    await repo.update_status(sample_forecast.forecast_id, ForecastStatus.INVALIDATED)

    # 3. Query current state (should be INVALIDATED)
    current = await repo.get(sample_forecast.forecast_id)
    assert current is not None
    assert current.status == ForecastStatus.INVALIDATED

    # 4. Query point-in-time query at future time (reconstructs original creation state from events)
    historical = await repo.point_in_time_query(
        sample_forecast.target, query_time=datetime.now(UTC) + timedelta(minutes=5)
    )
    assert historical is not None
    assert historical.forecast_id == sample_forecast.forecast_id
    assert historical.status == ForecastStatus.ACTIVE


@pytest.mark.asyncio
async def test_outcome_recording_and_unresolved_listing(
    test_session: AsyncSession, sample_forecast: Forecast
):
    """Verify outcome recording marks forecast resolved and updates audit trail."""
    f_repo = ForecastRepository(test_session)
    o_repo = OutcomeRepository(test_session)

    # Backdate expires_at so it falls in the past
    past_time = datetime.now(UTC) - timedelta(hours=2)
    sample_forecast.as_of = past_time - timedelta(hours=24)
    sample_forecast.expires_at = past_time
    sample_forecast.review_at = sample_forecast.as_of + timedelta(hours=1)
    await f_repo.create(sample_forecast)

    # List unresolved past horizon
    unresolved = await o_repo.list_unresolved(past_horizon=True)
    assert any(f.forecast_id == sample_forecast.forecast_id for f in unresolved)

    # Record ground truth outcome
    outcome = Outcome(
        outcome_id=uuid4(),
        forecast_id=sample_forecast.forecast_id,
        observed_value=2.60,
        event_occurred=None,
        resolved_at=datetime.now(UTC),
        resolution_method=ResolutionMethod.AUTOMATIC,
        ambiguity_note=None,
        resolution_rule_version="metric:error_rate:v1",
    )
    saved_outcome = await o_repo.record_outcome(outcome)
    assert saved_outcome.observed_value == 2.60

    # Verify forecast status transitioned to RESOLVED
    updated_forecast = await f_repo.get(sample_forecast.forecast_id)
    assert updated_forecast is not None
    assert updated_forecast.status == ForecastStatus.RESOLVED


@pytest.mark.asyncio
async def test_event_repository_append_only_enforcement(test_session: AsyncSession):
    """Verify ForecastEvent audit trail strictly raises on mutation or deletion."""
    event_repo = EventRepository(test_session)
    event = ForecastEvent(
        event_id=uuid4(),
        forecast_id=uuid4(),
        event_type=ForecastEventType.FORECAST_CREATED,
        payload={"message": "audit creation test"},
        emitted_at=datetime.now(UTC),
    )
    await event_repo.append(event)

    # Verify update and delete raise ReadOnlyAuditViolationError
    with pytest.raises(ReadOnlyAuditViolationError):
        await event_repo.update()

    with pytest.raises(ReadOnlyAuditViolationError):
        await event_repo.delete()


@pytest.mark.asyncio
async def test_model_promotion_gate_enforcement(test_session: AsyncSession):
    """Verify promoting a model without benchmark scores is strictly rejected."""
    model_repo = ModelRepository(test_session)

    # 1. Register unbenchmarked model
    unbenchmarked = ModelInfo(
        model_version="xgboost:unbenchmarked:v1",
        family="gradient_boosted",
        config_hash="hash123",
        promoted_at=datetime.now(UTC),
        benchmark_scores={},
    )
    await model_repo.register(unbenchmarked)

    # 2. Attempt promotion (Must fail)
    with pytest.raises(ModelPromotionError, match="has no benchmark scores attached"):
        await model_repo.promote("xgboost:unbenchmarked:v1")

    # 3. Register benchmarked model
    benchmarked = ModelInfo(
        model_version="auto_arima:benchmarked:v1",
        family="statsforecast.AutoARIMA",
        config_hash="hash456",
        promoted_at=datetime.now(UTC),
        benchmark_scores={"crps": 0.038, "coverage_90": 0.91},
    )
    await model_repo.register(benchmarked)

    # 4. Promote benchmarked model (Must succeed)
    promoted = await model_repo.promote("auto_arima:benchmarked:v1")
    assert promoted.model_version == "auto_arima:benchmarked:v1"

    active_models = await model_repo.current_active()
    assert len(active_models) == 1
    assert active_models[0].model_version == "auto_arima:benchmarked:v1"


@pytest.mark.asyncio
async def test_evaluation_repository_run_saving(test_session: AsyncSession):
    """Verify evaluation runs are stored and retrievable."""
    model_repo = ModelRepository(test_session)
    eval_repo = EvaluationRepository(test_session)

    model = ModelInfo(
        model_version="eval_test_model:v1",
        family="baseline.historic_mean",
        config_hash="eval_hash",
        promoted_at=datetime.now(UTC),
        benchmark_scores={"crps": 0.05},
    )
    await model_repo.register(model)

    run_id = await eval_repo.save_run(
        model_version="eval_test_model:v1",
        dataset_name="checkout_traffic_2026_q2",
        metrics={"crps": 0.041, "mape": 3.8},
    )
    assert run_id is not None

    latest = await eval_repo.latest_for_model("eval_test_model:v1")
    assert latest is not None
    assert latest["dataset_name"] == "checkout_traffic_2026_q2"
    assert latest["metrics"]["mape"] == 3.8
