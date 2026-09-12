"""Prompt 7: Comprehensive Acceptance Test Suite for Futuris.

Validates:
1. Structured FRIDAY TaskEnvelope with predictive distributions, intervals, calibration metrics, model metadata.
2. Cortex consumption without direct execution authority.
3. Stale-data detection producing BLOCKED / INSUFFICIENT_DATA status.
4. Insufficient-data detection producing INSUFFICIENT_DATA status.
5. Resolved forecasts updating calibration metrics (ECE, Brier score).
6. Invariant: no forecast response contains executable commands.
7. Invariant: prediction is not authorization (fail-closed HTTP 403 on command execution).
8. Timezone normalization and error handling.
9. Horizon mismatch validation.
10. Missing telemetry detection.
11. Backtest leakage detection (DataLeakageError).
12. Model drift detection via statistical process control limits.
13. Idempotency deduplication and task cancellation.
14. Explicit scenario specifications and assumptions.
15. Secure service authentication and immutable audit logging.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from futuris.api.app import app
from futuris.api.deps import get_db_session
from futuris.api.routers.friday import (
    FridayForecastRequest,
    FridayForecastResponse,
    FridayScenarioResponse,
    FridayTaskEnvelope,
)
from futuris.core.decision import (
    ActionSuggestion,
    AuthorizationViolationError,
    validate_prediction_authorization_separation,
)
from futuris.core.enums import ConfidenceLevel, ForecastStatus
from futuris.evaluation.backtest import DataLeakageError, validate_backtest_leakage
from futuris.evaluation.calibration import CalibrationAnalyzer, update_calibration_metrics
from futuris.evaluation.drift import DriftMonitor
from futuris.features.normalize import (
    DataStalenessError,
    HorizonMismatchError,
    InsufficientDataError,
    TimezoneNormalizationError,
    check_data_staleness,
    check_insufficient_data,
    check_missing_telemetry,
    normalize_timestamp,
    validate_horizon,
)
from futuris.infra.audit import AuditLogger
from futuris.storage.models import Base
from futuris.storage.repositories import ForecastRepository, OutcomeRepository


@pytest.fixture
async def futuris_test_db():
    """Isolated in-memory SQLite database for test runs."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def auth_headers(monkeypatch) -> dict[str, str]:
    """Provide authorized headers with mock FRIDAY API key."""
    api_key = "friday_secret_master_test_key"
    monkeypatch.setenv("FUTURIS_FRIDAY_API_KEY", api_key)
    monkeypatch.setenv("FUTURIS_API_KEY", "futuris_master_test_key")
    return {"X-API-Key": api_key}


@pytest.fixture
def cortex_traffic_fixture() -> dict:
    """Cortex 24h traffic forecasting inquiry payload."""
    return {
        "target": "cortex:traffic:visitor_rps_24h",
        "horizon": "24h",
        "confidence_level": 0.95,
        "context": {
            "site_id": "production_web_cluster",
            "current_rps": 180.0,
            "peak_threshold_rps": 400.0,
            "origin_service": "cortex_capacity_agent",
        },
    }


# ── TEST 1: Structured Forecast with Distributions, Intervals, Calibration ──
@pytest.mark.asyncio
async def test_friday_structured_forecast_with_uncertainty(futuris_test_db: AsyncSession, auth_headers):
    """Verify FRIDAY forecast returns predictive distribution, intervals, calibration and metadata."""
    async def _override_db():
        yield futuris_test_db

    app.dependency_overrides[get_db_session] = _override_db
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        req_payload = {
            "friday_request_id": f"friday_req_{uuid4().hex[:6]}",
            "target": "service:checkout:capacity_exceedance_24h",
            "horizon": "24h",
            "confidence_level": 0.90,
            "context": {"source": "friday_orchestrator"},
        }
        resp = await client.post("/v1/friday/forecast", headers=auth_headers, json=req_payload)
        assert resp.status_code == 201
        data = resp.json()

        # Uncertainty distributions
        assert "predictive_distribution" in data
        assert "p50" in data["predictive_distribution"]
        assert "p10" in data["predictive_distribution"]
        assert "p90" in data["predictive_distribution"]

        # Uncertainty intervals
        assert "intervals" in data
        assert len(data["intervals"]) > 0

        # Calibration metrics
        assert "calibration_metrics" in data
        assert "ece" in data["calibration_metrics"]
        assert "brier_score" in data["calibration_metrics"]

        # Model metadata
        assert "model_metadata" in data
        assert "model_version" in data["model_metadata"]

        # Invariants
        assert data["prediction_is_not_authorization"] is True
        assert data["executable_commands"] == []

    app.dependency_overrides.clear()


# ── TEST 2: Cortex Consumption Without Execution Authority ──
@pytest.mark.asyncio
async def test_cortex_consumption_without_execution(futuris_test_db: AsyncSession, auth_headers, cortex_traffic_fixture):
    """Verify Cortex consumes forecasts safely without receiving executable commands."""
    async def _override_db():
        yield futuris_test_db

    app.dependency_overrides[get_db_session] = _override_db
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Cortex calls delegation endpoint
        envelope = {
            "task_id": "cortex_traffic_planning_001",
            "source_agent": "cortex",
            "target_agent": "futuris",
            "action": "forecast",
            "payload": cortex_traffic_fixture,
            "priority": "urgent",
        }
        resp = await client.post("/v1/friday/delegate", headers=auth_headers, json=envelope)
        assert resp.status_code == 200
        data = resp.json()

        assert data["status"] == "SUCCESS"
        assert data["prediction_is_not_authorization"] is True
        assert data["executable_commands"] == []
        assert "prediction" in data["result"]

        # Verify Futuris does NOT permit Cortex or any caller to execute mitigations or website changes
        unauthorized_envelope = {
            "task_id": "cortex_malicious_exec_001",
            "source_agent": "cortex",
            "target_agent": "futuris",
            "action": "execute",
            "payload": {"command": "kubectl scale deployment cortex-web --replicas=10"},
        }
        err_resp = await client.post("/v1/friday/delegate", headers=auth_headers, json=unauthorized_envelope)
        assert err_resp.status_code == 403
        err_msg = err_resp.json().get("detail") or err_resp.json().get("error", {}).get("message", "")
        assert "Prediction is not authorization" in err_msg

    app.dependency_overrides.clear()


# ── TEST 3: Stale Data Produces BLOCKED Status ──
@pytest.mark.asyncio
async def test_stale_data_produces_blocked_or_insufficient_data(futuris_test_db: AsyncSession, auth_headers):
    """Verify stale telemetry triggers BLOCKED state and DataStalenessError."""
    # Direct library evaluation
    old_time = datetime.now(UTC) - timedelta(days=5)
    stale_observations = [
        {"timestamp": (old_time + timedelta(hours=i)).isoformat(), "value": 100.0}
        for i in range(10)
    ]
    with pytest.raises(DataStalenessError) as exc_info:
        check_data_staleness(stale_observations, max_staleness_seconds=3600.0, raise_error=True)
    assert "Data staleness detected" in str(exc_info.value)

    # API route evaluation
    async def _override_db():
        yield futuris_test_db

    app.dependency_overrides[get_db_session] = _override_db
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        req_payload = {
            "friday_request_id": "req_stale_check",
            "target": "service:checkout:capacity_exceedance_24h",
            "telemetry_data": stale_observations,
        }
        resp = await client.post("/v1/friday/forecast", headers=auth_headers, json=req_payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "BLOCKED"
        assert data["confidence"] == "INSUFFICIENT_DATA"
        assert "BLOCKED: Telemetry is stale" in data["assumptions"][0]

    app.dependency_overrides.clear()


# ── TEST 4: Insufficient Data Produces INSUFFICIENT_DATA Status ──
@pytest.mark.asyncio
async def test_insufficient_data_produces_insufficient_data_status(futuris_test_db: AsyncSession, auth_headers):
    """Verify telemetry with fewer than minimum points triggers INSUFFICIENT_DATA status."""
    # Direct library evaluation
    sparse_data = [{"timestamp": datetime.now(UTC).isoformat(), "value": 50.0}]
    with pytest.raises(InsufficientDataError) as exc_info:
        check_insufficient_data(sparse_data, min_points=5, raise_error=True)
    assert "below minimum required" in str(exc_info.value)

    # API route evaluation
    async def _override_db():
        yield futuris_test_db

    app.dependency_overrides[get_db_session] = _override_db
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        req_payload = {
            "friday_request_id": "req_sparse_check",
            "target": "service:checkout:capacity_exceedance_24h",
            "telemetry_data": sparse_data,
        }
        resp = await client.post("/v1/friday/forecast", headers=auth_headers, json=req_payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "INSUFFICIENT_DATA"
        assert data["confidence"] == "INSUFFICIENT_DATA"

    app.dependency_overrides.clear()


# ── TEST 5: Resolved Forecasts Update Calibration Metrics ──
@pytest.mark.asyncio
async def test_resolved_forecasts_update_calibration_metrics(futuris_test_db: AsyncSession, auth_headers):
    """Verify ground-truth outcomes update calibration metrics dynamically."""
    async def _override_db():
        yield futuris_test_db

    app.dependency_overrides[get_db_session] = _override_db
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Create a forecast
        f_resp = await client.post(
            "/v1/friday/forecast",
            headers=auth_headers,
            json={"target": "service:checkout:capacity_exceedance_24h"},
        )
        assert f_resp.status_code == 201
        forecast_id = f_resp.json()["futuris_forecast_id"]

        # 2. Resolve forecast with ground truth
        resolve_payload = {
            "forecast_id": forecast_id,
            "observed_value": 115.5,
            "event_occurred": True,
            "ambiguity_note": "Post-event telemetry confirmed capacity exceedance",
        }
        res = await client.post("/v1/friday/resolution", headers=auth_headers, json=resolve_payload)
        assert res.status_code == 200
        res_data = res.json()

        assert res_data["status"] == "RESOLVED"
        assert res_data["observed_value"] == 115.5
        assert "updated_calibration_metrics" in res_data
        assert "ece" in res_data["updated_calibration_metrics"]
        assert "brier_score" in res_data["updated_calibration_metrics"]

    app.dependency_overrides.clear()


# ── TEST 6: Invariant: No Forecast Response Contains Executable Commands ──
def test_no_forecast_response_contains_executable_commands():
    """Verify schema-level validators strictly forbid non-empty executable commands."""
    # 1. FridayForecastResponse forbids commands
    with pytest.raises(ValidationError):
        FridayForecastResponse(
            futuris_forecast_id=uuid4(),
            friday_request_id="req_001",
            prediction={"point_estimate": 100.0, "lower_bound": 90.0, "upper_bound": 110.0, "probability_distribution": {}},
            confidence="HIGH",
            calibration_score=0.04,
            evidence_snapshot_id="snap_01",
            model_used="auto_ets",
            drivers_identified=[],
            executable_commands=["rm -rf /", "kubectl delete pods --all"],
        )

    # 2. ActionSuggestion forbids commands
    with pytest.raises(AuthorizationViolationError):
        ActionSuggestion(
            action_type="scale_pods",
            target="checkout_service",
            rationale="Traffic spike impending",
            estimated_mitigation_effect="Reduced latency",
            executable_commands=["sudo systemctl restart cluster"],
        )

    # 3. FridayScenarioResponse forbids commands
    with pytest.raises(ValidationError):
        FridayScenarioResponse(
            scenario_id=uuid4(),
            divergent_prediction=200.0,
            probability_outcome=0.9,
            risk_assessment="HIGH_RISK",
            comparison_to_baseline={},
            executable_commands=["deploy_hotfix.sh"],
        )


# ── TEST 7: Prediction Is Not Authorization Invariant ──
@pytest.mark.asyncio
async def test_prediction_is_not_authorization_invariant(auth_headers):
    """Verify direct command execution attempts fail-closed with 403 or AuthorizationViolationError."""
    # Direct function checks
    for cmd in ["sudo reboot", "kubectl scale deploy", "terraform apply", "patch_website"]:
        with pytest.raises(AuthorizationViolationError):
            validate_prediction_authorization_separation(cmd)

    # API endpoints checks
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Delegation endpoint
        bad_resp = await client.post(
            "/v1/friday/delegate",
            headers=auth_headers,
            json={"action": "apply_mitigation", "payload": {"command": "scale 5"}},
        )
        assert bad_resp.status_code == 403

        # Universal Task Protocol endpoint
        bad_task = await client.post(
            "/v1/task/execute",
            json={"action": "website_change", "payload": {"commands": ["deploy_new_theme"]}},
        )
        assert bad_task.status_code == 403


# ── TEST 8: Timezone Normalization and Error Handling ──
def test_timezone_normalization_and_error_handling():
    """Verify timestamps across various formats are normalized to UTC or raise errors."""
    # UTC ISO string
    dt1 = normalize_timestamp("2026-09-12T12:00:00Z")
    assert dt1.tzinfo == UTC
    assert dt1.hour == 12

    # Offset ISO string (+05:30 -> -5h30m in UTC)
    dt2 = normalize_timestamp("2026-09-12T17:30:00+05:30")
    assert dt2.tzinfo == UTC
    assert dt2.hour == 12
    assert dt2.minute == 0

    # Naive datetime gets converted to UTC
    naive = datetime(2026, 9, 12, 10, 0, 0)
    dt3 = normalize_timestamp(naive)
    assert dt3.tzinfo == UTC

    # Malformed timestamp string
    with pytest.raises(TimezoneNormalizationError):
        normalize_timestamp("invalid-date-format-string")


# ── TEST 9: Horizon Mismatch Validation ──
def test_horizon_mismatch_validation():
    """Verify temporal start and end boundaries are strictly checked."""
    now = datetime.now(UTC)

    # End before start raises error
    with pytest.raises(HorizonMismatchError):
        validate_horizon(start_time=now, end_time=now - timedelta(hours=1))

    # Horizon too short (< 5 minutes) raises error
    with pytest.raises(HorizonMismatchError):
        validate_horizon(start_time=now, end_time=now + timedelta(seconds=30))

    # Valid horizon returns timedelta
    delta = validate_horizon(start_time=now, end_time=now + timedelta(hours=24))
    assert delta == timedelta(hours=24)


# ── TEST 10: Missing Telemetry Gap Detection ──
def test_missing_telemetry_detection():
    """Verify dropout gaps between sequential telemetry points are flagged."""
    base = datetime.now(UTC)

    # Normal 5m interval points
    normal_series = [base + timedelta(minutes=5 * i) for i in range(10)]
    assert check_missing_telemetry(normal_series, max_allowed_gap=timedelta(hours=1)) is False

    # Series with a 3h blackout gap
    gapped_series = [
        base,
        base + timedelta(minutes=5),
        base + timedelta(hours=4),  # 3h55m gap
        base + timedelta(hours=4, minutes=5),
    ]
    with pytest.raises(InsufficientDataError) as exc_info:
        check_missing_telemetry(gapped_series, max_allowed_gap=timedelta(hours=1))
    assert "Missing telemetry gap" in str(exc_info.value)


# ── TEST 11: Backtest Leakage Detection ──
def test_backtest_leakage_detection():
    """Verify future features or target values leaking past as_of raise DataLeakageError."""
    as_of = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)

    # Valid strictly historical points
    valid_features = [as_of - timedelta(hours=2), as_of - timedelta(hours=1), as_of]
    assert validate_backtest_leakage(valid_features, as_of=as_of) is True

    # Leaked point in the future relative to as_of
    leaked_features = [as_of - timedelta(hours=1), as_of + timedelta(seconds=1)]
    with pytest.raises(DataLeakageError) as exc_info:
        validate_backtest_leakage(leaked_features, as_of=as_of)
    assert "leakage detected" in str(exc_info.value)


# ── TEST 12: Model Drift Detection ──
def test_model_drift_detection():
    """Verify DriftMonitor flags 3-sigma degradation on out-of-control evaluation scores."""
    monitor = DriftMonitor()
    historical_scores = [0.10, 0.12, 0.11, 0.10, 0.13, 0.11, 0.12]

    # Nominal recent scores
    normal_status = monitor.evaluate_drift("auto_ets@v1", historical_scores, recent_scores=[0.11])
    assert normal_status.is_degraded is False

    # Extreme degraded recent score (3-sigma breach)
    degraded_status = monitor.evaluate_drift("auto_ets@v1", historical_scores, recent_scores=[0.85])
    assert degraded_status.is_degraded is True
    assert degraded_status.current_score > degraded_status.control_limit


# ── TEST 13: Idempotency Deduplication and Task Cancellation ──
@pytest.mark.asyncio
async def test_idempotency_and_task_cancellation(futuris_test_db: AsyncSession, auth_headers):
    """Verify idempotent forecast creation returns identical IDs, and cancellation updates status."""
    async def _override_db():
        yield futuris_test_db

    app.dependency_overrides[get_db_session] = _override_db
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        idemp_key = f"idem_test_{uuid4().hex[:8]}"
        req_payload = {
            "friday_request_id": "req_first_call",
            "target": "service:checkout:capacity_exceedance_24h",
            "idempotency_key": idemp_key,
        }

        # 1. First call: creates forecast
        resp1 = await client.post("/v1/friday/forecast", headers=auth_headers, json=req_payload)
        assert resp1.status_code == 201
        data1 = resp1.json()
        forecast_id1 = data1["futuris_forecast_id"]

        # 2. Second call with same idempotency key: returns cached forecast
        req_payload["friday_request_id"] = "req_second_call"
        resp2 = await client.post("/v1/friday/forecast", headers=auth_headers, json=req_payload)
        assert resp2.status_code == 201
        data2 = resp2.json()
        forecast_id2 = data2["futuris_forecast_id"]

        assert forecast_id1 == forecast_id2

        # 3. Cancel the forecast
        cancel_resp = await client.post(
            f"/v1/friday/forecasts/{forecast_id1}/cancel",
            headers=auth_headers,
        )
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "CANCELLED"

    app.dependency_overrides.clear()


# ── TEST 14: Explicit Scenario Specifications and Assumptions ──
@pytest.mark.asyncio
async def test_explicit_scenario_specifications_and_assumptions(futuris_test_db: AsyncSession, auth_headers):
    """Verify counterfactual scenario evaluation records explicit assumptions and specs."""
    async def _override_db():
        yield futuris_test_db

    app.dependency_overrides[get_db_session] = _override_db
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Base forecast
        f_resp = await client.post(
            "/v1/friday/forecast",
            headers=auth_headers,
            json={"target": "service:checkout:capacity_exceedance_24h"},
        )
        base_id = f_resp.json()["futuris_forecast_id"]

        # Scenario evaluation
        scen_payload = {
            "question": "What if payment gateway latency increases 50%?",
            "base_forecast_id": base_id,
            "scenario_spec": {
                "name": "gateway_degradation_stress_test",
                "variable_changes": [{"metric": "latency", "change_pct": 50.0}],
                "assumptions": [
                    "Payment provider SLA breached",
                    "Client retries surge 3x",
                    "Database connection pool saturated",
                ],
                "scenario_type": "stress",
            },
        }
        s_resp = await client.post("/v1/friday/scenario", headers=auth_headers, json=scen_payload)
        assert s_resp.status_code == 200
        s_data = s_resp.json()

        assert "divergent_prediction" in s_data
        assert "risk_assessment" in s_data
        assert s_data["risk_assessment"] in ["HIGH_RISK", "MODERATE_RISK"]
        assert len(s_data["assumptions"]) == 3
        assert "Payment provider SLA breached" in s_data["assumptions"]
        assert s_data["prediction_is_not_authorization"] is True
        assert s_data["executable_commands"] == []

    app.dependency_overrides.clear()


# ── TEST 15: Secure Service Authentication and Audit Logging ──
@pytest.mark.asyncio
async def test_secure_service_auth_and_audit_logging(futuris_test_db: AsyncSession, auth_headers):
    """Verify endpoint authentication security and immutable audit logging on state mutations."""
    async def _override_db():
        yield futuris_test_db

    app.dependency_overrides[get_db_session] = _override_db
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Unauthorized without key -> 401
        res_no_auth = await client.post(
            "/v1/friday/forecast",
            json={"target": "service:checkout:capacity_exceedance_24h"},
        )
        assert res_no_auth.status_code == 401

        # Unauthorized with wrong key -> 401
        res_bad_auth = await client.post(
            "/v1/friday/forecast",
            headers={"X-API-Key": "wrong_invalid_key"},
            json={"target": "service:checkout:capacity_exceedance_24h"},
        )
        assert res_bad_auth.status_code == 401

        # Authorized request succeeds and writes audit log
        res_auth = await client.post(
            "/v1/friday/forecast",
            headers=auth_headers,
            json={"target": "service:checkout:capacity_exceedance_24h"},
        )
        assert res_auth.status_code == 201

        # Verify audit log recorded via AuditLogger
        audit_logger = AuditLogger(futuris_test_db)
        history = await audit_logger.get_entity_history("forecast", res_auth.json()["futuris_forecast_id"])
        assert len(history) >= 1
        assert history[0].action == "generate_forecast"
        assert len(history[0].payload_hash) == 64  # SHA-256 length

    app.dependency_overrides.clear()
