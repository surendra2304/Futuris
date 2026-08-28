"""Comprehensive tests for core domain schemas, enums, validators, and serialization."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pandas as pd
import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from futuris.core import (
    ConfidenceLevel,
    Driver,
    EvidenceRef,
    Forecast,
    ForecastEvent,
    ForecastEventType,
    ForecastStatus,
    HorizonBucket,
    ModelInfo,
    Outcome,
    ResolutionMethod,
    Scenario,
    ScenarioType,
    SignalClass,
    SourceTrust,
)
from futuris.core.lifecycle import LifecycleManager
from futuris.core.resolution import OutcomeResolver
from futuris.core.thresholds import AlertThreshold, ThresholdMonitor
from futuris.infra.events import (
    EventEmitter,
    WebhookSubscription,
    sign_payload,
)
from futuris.storage.models import Base
from futuris.storage.repositories import (
    EventRepository,
    ForecastRepository,
    OutcomeRepository,
)


@pytest.fixture
def base_evidence() -> EvidenceRef:
    """Fixture providing a standard frozen evidence snapshot."""
    return EvidenceRef(
        evidence_id=uuid4(),
        source="telemetry:datadog:checkout",
        source_trust=SourceTrust.HIGH,
        signal_class=SignalClass.TELEMETRY,
        as_of=datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC),
        snapshot_path="/data/storage/evidence/2026-08-28/snap1.parquet",
        content_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )


@pytest.fixture
def valid_forecast_payload(base_evidence: EvidenceRef) -> dict:
    """Fixture providing a valid forecast initialization payload."""
    as_of = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)
    return {
        "forecast_id": uuid4(),
        "target": "service:checkout:capacity_exceedance_24h",
        "as_of": as_of,
        "horizon": timedelta(hours=24),
        "expires_at": as_of + timedelta(hours=24),
        "prediction": 85.5,
        "range_lower": 70.0,
        "range_upper": 95.0,
        "probability": 0.78,
        "confidence": ConfidenceLevel.MEDIUM,
        "drivers": [
            Driver(
                name="traffic_spike_lead",
                direction="positive",
                strength=0.82,
                leading_or_lagging="leading",
                evidence_refs=[base_evidence.evidence_id],
            )
        ],
        "evidence": [base_evidence],
        "model_version": "adapter:statsforecast:auto_arima:c8f93a",
        "assumptions": ["system architecture unchanged", "db replica lag < 50ms"],
        "review_at": as_of + timedelta(hours=6),
        "status": ForecastStatus.ACTIVE,
        "scenario_id": None,
    }


def test_forecast_happy_path_instantiation(valid_forecast_payload: dict):
    """Test valid forecast instantiation with all fields populated."""
    forecast = Forecast(**valid_forecast_payload)
    assert forecast.target == "service:checkout:capacity_exceedance_24h"
    assert forecast.prediction == 85.5
    assert forecast.confidence == ConfidenceLevel.MEDIUM
    assert len(forecast.drivers) == 1
    assert len(forecast.evidence) == 1


def test_forecast_json_serialization_round_trip(valid_forecast_payload: dict):
    """Test model_dump and model_validate round-trip serialization."""
    forecast = Forecast(**valid_forecast_payload)
    json_data = forecast.model_dump(mode="json")
    assert isinstance(json_data["forecast_id"], str)
    assert json_data["status"] == "active"
    assert json_data["confidence"] == "medium"

    reconstructed = Forecast.model_validate(json_data)
    assert reconstructed.forecast_id == forecast.forecast_id
    assert reconstructed.prediction == forecast.prediction
    assert reconstructed.probability == forecast.probability


def test_validator_probability_bounds_valid(valid_forecast_payload: dict):
    """Test boundary values for probability in [0, 1]."""
    valid_forecast_payload["probability"] = 0.0
    f1 = Forecast(**valid_forecast_payload)
    assert f1.probability == 0.0

    valid_forecast_payload["probability"] = 1.0
    f2 = Forecast(**valid_forecast_payload)
    assert f2.probability == 1.0

    valid_forecast_payload["probability"] = None
    f3 = Forecast(**valid_forecast_payload)
    assert f3.probability is None


def test_validator_probability_out_of_bounds_negative(valid_forecast_payload: dict):
    """Test error raised when probability is negative."""
    valid_forecast_payload["probability"] = -0.01
    with pytest.raises(ValidationError, match="Probability must be between 0.0 and 1.0"):
        Forecast(**valid_forecast_payload)


def test_validator_probability_out_of_bounds_excess(valid_forecast_payload: dict):
    """Test error raised when probability exceeds 1.0."""
    valid_forecast_payload["probability"] = 1.05
    with pytest.raises(ValidationError, match="Probability must be between 0.0 and 1.0"):
        Forecast(**valid_forecast_payload)


def test_validator_range_ordering_lower_violation(valid_forecast_payload: dict):
    """Test error raised when prediction is lower than range_lower."""
    valid_forecast_payload["range_lower"] = 90.0
    valid_forecast_payload["prediction"] = 85.0
    valid_forecast_payload["range_upper"] = 100.0
    with pytest.raises(ValidationError, match="Range bounds violation"):
        Forecast(**valid_forecast_payload)


def test_validator_range_ordering_upper_violation(valid_forecast_payload: dict):
    """Test error raised when prediction exceeds range_upper."""
    valid_forecast_payload["range_lower"] = 50.0
    valid_forecast_payload["prediction"] = 95.0
    valid_forecast_payload["range_upper"] = 90.0
    with pytest.raises(ValidationError, match="Range bounds violation"):
        Forecast(**valid_forecast_payload)


def test_validator_temporal_ordering_as_of_expires_at(valid_forecast_payload: dict):
    """Test error raised when as_of >= expires_at."""
    valid_forecast_payload["expires_at"] = valid_forecast_payload["as_of"]
    with pytest.raises(ValidationError, match="Temporal violation: as_of .* must precede"):
        Forecast(**valid_forecast_payload)


def test_validator_horizon_strictly_positive(valid_forecast_payload: dict):
    """Test error raised when horizon is zero or negative."""
    valid_forecast_payload["horizon"] = timedelta(seconds=0)
    with pytest.raises(ValidationError, match="Forecast horizon must be strictly positive"):
        Forecast(**valid_forecast_payload)

    valid_forecast_payload["horizon"] = timedelta(hours=-1)
    with pytest.raises(ValidationError, match="Forecast horizon must be strictly positive"):
        Forecast(**valid_forecast_payload)


def test_validator_review_at_temporal_integrity(valid_forecast_payload: dict):
    """Test error raised when review_at precedes as_of."""
    valid_forecast_payload["review_at"] = valid_forecast_payload["as_of"] - timedelta(minutes=10)
    with pytest.raises(ValidationError, match="Review schedule violation: review_at"):
        Forecast(**valid_forecast_payload)


def test_validator_driver_evidence_ref_cross_field(valid_forecast_payload: dict):
    """Test cross-field validation ensuring driver evidence_refs exist in forecast.evidence."""
    orphan_evidence_id = uuid4()
    valid_forecast_payload["drivers"][0].evidence_refs.append(orphan_evidence_id)
    with pytest.raises(ValidationError, match="references evidence_id .* not present"):
        Forecast(**valid_forecast_payload)


def test_confidence_probability_orthogonality(valid_forecast_payload: dict):
    """Assert high probability can co-exist with low confidence (meta-calibration quality)."""
    valid_forecast_payload["probability"] = 0.95
    valid_forecast_payload["confidence"] = ConfidenceLevel.LOW
    forecast = Forecast(**valid_forecast_payload)
    assert forecast.probability == 0.95
    assert forecast.confidence == ConfidenceLevel.LOW
    # Assert docs explicitly document the distinction
    assert "META-confidence" in Forecast.__doc__
    assert "independent" in Forecast.__doc__


def test_outcome_schema_and_serialization():
    """Test Outcome creation and serialization."""
    forecast_id = uuid4()
    outcome = Outcome(
        forecast_id=forecast_id,
        observed_value=88.2,
        event_occurred=True,
        resolved_at=datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC),
        resolution_method=ResolutionMethod.AUTOMATIC,
        ambiguity_note=None,
        resolution_rule_version="rule:exceedance:v1.2",
    )
    dumped = outcome.model_dump(mode="json")
    assert dumped["resolution_method"] == "automatic"
    assert dumped["event_occurred"] is True
    validated = Outcome.model_validate(dumped)
    assert validated.forecast_id == forecast_id
    assert validated.observed_value == 88.2


def test_scenario_schema_and_serialization():
    """Test Scenario creation and counterfactual branch definition."""
    parent_id = uuid4()
    scenario = Scenario(
        name="Black Friday 3x Load Stress",
        scenario_type=ScenarioType.STRESS,
        assumptions_override={"traffic_multiplier": 3.0, "external_gateway_latency_ms": 250},
        created_by="agent:scenario_simulator_v1",
        parent_forecast_id=parent_id,
    )
    dumped = scenario.model_dump(mode="json")
    assert dumped["scenario_type"] == "stress"
    assert dumped["assumptions_override"]["traffic_multiplier"] == 3.0
    validated = Scenario.model_validate(dumped)
    assert validated.parent_forecast_id == parent_id


def test_forecast_event_and_model_info():
    """Test ForecastEvent emission and ModelInfo registration schemas."""
    f_id = uuid4()
    event = ForecastEvent(
        forecast_id=f_id,
        event_type=ForecastEventType.FORECAST_CREATED,
        payload={"target": "service:checkout", "prediction": 85.5},
        emitted_at=datetime.now(UTC),
    )
    assert event.event_type == ForecastEventType.FORECAST_CREATED
    assert event.forecast_id == f_id

    model_info = ModelInfo(
        model_version="auto_arima:v1.0",
        family="statsforecast.AutoARIMA",
        config_hash="a1b2c3d4e5f6",
        promoted_at=datetime.now(UTC),
        benchmark_scores={"crps": 0.042, "mape": 4.1, "coverage_90": 0.89},
    )
    assert model_info.family == "statsforecast.AutoARIMA"
    assert model_info.benchmark_scores["crps"] == 0.042
    assert HorizonBucket.DAYS == "days"


# --- Phase 7: Outcome Resolution, Lifecycle, Thresholds & Webhook Tests ---


@pytest_asyncio.fixture
async def lifecycle_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


def test_outcome_resolver_true_false_and_ambiguous_paths():
    """Verify outcome resolution logic for true, false, and data gap ambiguity."""
    resolver = OutcomeResolver()
    as_of = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)
    evidence_id = uuid4()
    f = Forecast(
        forecast_id=uuid4(),
        target="service:checkout:capacity_exceedance_24h",
        as_of=as_of,
        horizon=timedelta(hours=24),
        expires_at=as_of + timedelta(hours=24),
        prediction=3500.0,
        range_lower=3000.0,
        range_upper=4200.0,
        probability=0.75,
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
        model_version="auto_arima@v1:hash",
        assumptions=["stable architecture"],
        review_at=as_of + timedelta(hours=6),
        status=ForecastStatus.ACTIVE,
    )
    timestamps = [as_of + timedelta(minutes=5 * i) for i in range(1, 289)]

    # 1. True path: max demand exceeds 4000.0
    values_exceed = [3500.0] * 280 + [4200.0] * 8
    df_true = pd.DataFrame({"timestamp": timestamps, "value": values_exceed})
    outcome_true = resolver.resolve_forecast(f, df_true)
    assert outcome_true.event_occurred is True
    assert outcome_true.observed_value == 4200.0
    assert outcome_true.resolution_method == ResolutionMethod.AUTOMATIC

    # 2. False path: max demand strictly below 4000.0
    values_below = [3500.0] * 288
    df_false = pd.DataFrame({"timestamp": timestamps, "value": values_below})
    outcome_false = resolver.resolve_forecast(f, df_false)
    assert outcome_false.event_occurred is False
    assert outcome_false.observed_value == 3500.0
    assert outcome_false.resolution_method == ResolutionMethod.AUTOMATIC

    # 3. Ambiguous path: major observation gaps (> 20%)
    df_gaps = pd.DataFrame({"timestamp": timestamps[:50], "value": values_below[:50]})
    outcome_ambiguous = resolver.resolve_forecast(f, df_gaps)
    assert outcome_ambiguous.resolution_method == ResolutionMethod.AMBIGUOUS
    assert outcome_ambiguous.event_occurred is None
    assert "Excessive observation gaps" in (outcome_ambiguous.ambiguity_note or "")


@pytest.mark.asyncio
async def test_lifecycle_manager_assumption_invalidation_and_resolution(
    lifecycle_session: AsyncSession,
):
    """Verify assumption breaks invalidate forecasts and elapsed horizons resolve outcomes."""
    f_repo = ForecastRepository(lifecycle_session)
    o_repo = OutcomeRepository(lifecycle_session)
    e_repo = EventRepository(lifecycle_session)
    manager = LifecycleManager(f_repo, o_repo, e_repo)

    as_of = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)
    evidence_id = uuid4()
    f = Forecast(
        forecast_id=uuid4(),
        target="service:checkout:capacity_exceedance_24h",
        as_of=as_of,
        horizon=timedelta(hours=24),
        expires_at=as_of + timedelta(hours=24),
        prediction=3500.0,
        range_lower=3000.0,
        range_upper=4200.0,
        probability=0.75,
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
        model_version="auto_arima@v1:hash",
        assumptions=["stable architecture"],
        review_at=as_of + timedelta(hours=6),
        status=ForecastStatus.ACTIVE,
    )
    await f_repo.create(f)

    # Invalidation on assumption break
    capacity_events = [
        {
            "timestamp": as_of + timedelta(hours=2),
            "new_capacity": 3000.0,
        }
    ]
    timestamps = [as_of + timedelta(minutes=5 * i) for i in range(1, 289)]
    df_obs = pd.DataFrame({"timestamp": timestamps, "value": [3200.0] * 288})

    report_inv = await manager.run_lifecycle_sweep(
        observations_df=df_obs,
        capacity_events=capacity_events,
        as_of=as_of + timedelta(hours=5),
    )
    assert report_inv.invalidated_count == 1

    inv_forecast = await f_repo.get(f.forecast_id)
    assert inv_forecast is not None
    assert inv_forecast.status == ForecastStatus.INVALIDATED

    # Resolution on elapsed horizon
    ev2_id = uuid4()
    f2 = f.model_copy(
        update={
            "forecast_id": uuid4(),
            "status": ForecastStatus.ACTIVE,
            "evidence": [f.evidence[0].model_copy(update={"evidence_id": ev2_id})],
            "drivers": [f.drivers[0].model_copy(update={"evidence_refs": [ev2_id]})],
        }
    )
    await f_repo.create(f2)

    report_res = await manager.run_lifecycle_sweep(
        observations_df=df_obs,
        as_of=f2.expires_at + timedelta(minutes=10),
    )
    assert report_res.resolved_count == 1
    assert len(report_res.outcomes) == 1
    assert report_res.outcomes[0].forecast_id == f2.forecast_id


@pytest.mark.asyncio
async def test_threshold_monitor_deduplication(lifecycle_session: AsyncSession):
    """Verify ThresholdMonitor fires exactly once for crossing a probability threshold."""
    e_repo = EventRepository(lifecycle_session)
    monitor = ThresholdMonitor(event_repo=e_repo)

    thresh_id = uuid4()
    target_name = "service:checkout:capacity_exceedance_24h"
    monitor.register_threshold(
        AlertThreshold(
            threshold_id=thresh_id,
            target=target_name,
            probability_floor=0.70,
            direction="above",
        )
    )

    as_of = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)
    evidence_id = uuid4()
    f = Forecast(
        forecast_id=uuid4(),
        target=target_name,
        as_of=as_of,
        horizon=timedelta(hours=24),
        expires_at=as_of + timedelta(hours=24),
        prediction=3500.0,
        range_lower=3000.0,
        range_upper=4200.0,
        probability=0.75,
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
        model_version="auto_arima@v1:hash",
        assumptions=["stable architecture"],
        review_at=as_of + timedelta(hours=6),
        status=ForecastStatus.ACTIVE,
    )

    # First evaluation: probability (0.75) >= 0.70 -> Fires event
    events_1 = await monitor.evaluate_forecast(f)
    assert len(events_1) == 1
    assert events_1[0].event_type == ForecastEventType.FORECAST_THRESHOLD_CROSSED

    # Second evaluation on same forecast: De-duplicated -> 0 events
    events_2 = await monitor.evaluate_forecast(f)
    assert len(events_2) == 0


@pytest.mark.asyncio
async def test_hmac_webhook_signature_and_dispatch():
    """Verify HMAC signature generation and webhook dispatch to mock endpoint."""
    secret = "super_secret_signing_key_123"
    payload = {"event": "test", "value": 42}
    signature = sign_payload(payload, secret)

    assert len(signature) == 64

    received_requests = []

    def mock_handler(request: httpx.Request):
        received_requests.append(request)
        return httpx.Response(200, json={"status": "received"})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        emitter = EventEmitter(http_client=client)
        emitter.register_subscription(
            WebhookSubscription(
                subscription_id=uuid4(),
                url="http://mock-webhook/receive",
                event_types=[ForecastEventType.FORECAST_THRESHOLD_CROSSED],
                secret=secret,
            )
        )

        test_event = ForecastEvent(
            event_id=uuid4(),
            forecast_id=uuid4(),
            event_type=ForecastEventType.FORECAST_THRESHOLD_CROSSED,
            payload=payload,
            emitted_at=datetime.now(UTC),
        )
        await emitter.emit(test_event)

    assert len(received_requests) == 1
    req = received_requests[0]
    assert req.headers["X-Futuris-Event-Type"] == "forecast_threshold_crossed"
    expected_sig = sign_payload(test_event.model_dump(mode="json"), secret)
    assert req.headers["X-Futuris-Signature"] == expected_sig
