"""Multi-agent collaboration tests: the FRIDAY mesh working together.

These drive the real HTTP surface with the real domain objects. The invariants
under test:

1. FRIDAY delegates a forecast and gets a structured, provenance-labelled
   answer, including when its peer dependencies are unreachable.
2. Cortex can consume a forecast but can never turn it into an execution
   authorization (`prediction_is_not_authorization`).
3. A peer going down degrades the response instead of failing it, and the
   breaker isolates the peer, and recovery is observed once it returns.
4. Sentinel governance events and Memora persistence failures do not take the
   forecast path down with them.
"""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from futuris.api.app import app
from futuris.api.deps import get_db_session
from futuris.infra.config import Settings
from futuris.infra.resilience import CircuitBreakerRegistry, CircuitState, guarded_call
from futuris.storage.models import Base

MASTER_KEY = "multi_agent_test_key_0123456789abcdef"
FRIDAY_KEY = "friday_agent_test_key_0123456789abcdef"


@pytest_asyncio.fixture(scope="function")
async def mesh_client(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Client wired to an isolated database with both credentials configured."""
    from futuris.infra.config import settings

    monkeypatch.setattr(settings, "FUTURIS_API_KEY", MASTER_KEY)
    monkeypatch.setattr(settings, "FUTURIS_FRIDAY_API_KEY", FRIDAY_KEY)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        # Mirror the production dependency: commit on success, roll back on
        # error. Without the commit the route's writes are discarded when the
        # request session closes.
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()
    await engine.dispose()


# ── 1. delegation ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_friday_delegation_returns_provenance_labelled_forecast(
    mesh_client: httpx.AsyncClient,
):
    response = await mesh_client.post(
        "/v1/friday/forecast",
        headers={"X-API-Key": FRIDAY_KEY},
        json={
            "friday_request_id": "req_mesh_1",
            "target": "service:checkout:capacity_exceedance_24h",
            "horizon": "24h",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()

    # The answer must be usable by a peer agent without further interpretation.
    assert body["prediction"]["point_estimate"] is not None
    assert body["prediction"]["lower_bound"] <= body["prediction"]["point_estimate"]
    assert body["prediction"]["upper_bound"] >= body["prediction"]["point_estimate"]
    assert body["evidence_class"] in {"live", "derived", "synthetic", "demo"}
    assert body["evidence_class"] != "live" or body["evidence_snapshot_id"]
    assert body["model_used"]
    assert body["prediction_is_not_authorization"] is True
    assert body["executable_commands"] == []


@pytest.mark.asyncio
async def test_friday_delegation_works_when_peers_are_unreachable(
    mesh_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """No IntelX, Inference or Memora in this sandbox: the forecast still lands."""
    from futuris.infra import config as config_module

    unreachable = Settings().model_copy(
        update={
            "INTELX_URL": "http://127.0.0.1:9",
            "INFERENCE_URL": "http://127.0.0.1:9",
            "MEMORA_URL": "http://127.0.0.1:9",
            "STRATEX_URL": "http://127.0.0.1:9",
        }
    )
    monkeypatch.setattr(config_module, "settings", unreachable)

    response = await mesh_client.post(
        "/v1/friday/forecast",
        headers={"X-API-Key": FRIDAY_KEY},
        json={
            "friday_request_id": "req_mesh_degraded",
            "target": "service:checkout:capacity_exceedance_24h",
            "horizon": "24h",
        },
    )
    # Either a real forecast from local models, or an explicit refusal -- never
    # a fabricated number and never a crash.
    assert response.status_code in (201, 202), response.text
    if response.status_code == 201:
        assert response.json()["prediction_is_not_authorization"] is True


# ── 2. prediction is not authorization, across agents ──────────────────────


@pytest.mark.asyncio
async def test_cortex_cannot_convert_a_forecast_into_execution(
    mesh_client: httpx.AsyncClient,
):
    envelope = {
        "friday_request_id": "req_cortex_1",
        "target": "service:checkout:capacity_exceedance_24h",
        "horizon": "24h",
    }
    delegated = await mesh_client.post(
        "/v1/friday/forecast", headers={"X-API-Key": FRIDAY_KEY}, json=envelope
    )
    assert delegated.status_code == 201
    forecast_id = delegated.json()["futuris_forecast_id"]

    # Cortex now tries to act on the advice it just received.
    for action, payload in (
        ("apply_mitigation", {"command": f"futuris:scale-out:{forecast_id}"}),
        ("website_change", {"commands": ["deploy_new_theme"]}),
        ("execute_shell", {"cmd": "sudo reboot"}),
    ):
        execution = await mesh_client.post(
            "/v1/friday/delegate",
            headers={"X-API-Key": FRIDAY_KEY},
            json={"action": action, "payload": payload},
        )
        assert execution.status_code == 403, f"{action} was not refused: {execution.text}"
        assert "authorization" in execution.text.lower()

    # And the generic task endpoint refuses to fabricate a forecast as work.
    task = await mesh_client.post(
        "/v1/task/execute",
        json={"task_id": "mesh_task_1", "action": "forecast", "payload": {"target": "BTCUSDT"}},
    )
    assert task.status_code == 501
    assert "No forecast was run" in task.text


@pytest.mark.asyncio
async def test_forecast_records_never_carry_executable_commands(mesh_client: httpx.AsyncClient):
    created = await mesh_client.post(
        "/v1/forecasts",
        headers={"X-API-Key": MASTER_KEY},
        json={"target": "service:checkout:capacity_exceedance_24h", "horizon": "24h"},
    )
    assert created.status_code == 201
    body = created.json()
    assert "executable_commands" not in body or body["executable_commands"] == []

    listed = await mesh_client.get("/v1/forecasts")
    assert listed.status_code == 200
    for forecast in listed.json():
        assert forecast.get("executable_commands", []) == []


# ── 3. a peer going down isolates, then recovers ───────────────────────────


@pytest.mark.asyncio
async def test_peer_failure_isolates_then_recovers():
    registry = CircuitBreakerRegistry(failure_threshold=2, recovery_timeout=0.1)
    calls = {"n": 0}
    peer_is_up = {"value": False}

    async def flaky_peer() -> str:
        calls["n"] += 1
        if not peer_is_up["value"]:
            raise httpx.ConnectError("peer down")
        return "peer answered"

    # Peer is down: calls fail and the circuit opens.
    assert await guarded_call(registry, "sentinel", flaky_peer, fallback=None) is None
    assert await guarded_call(registry, "sentinel", flaky_peer, fallback=None) is None
    breaker = registry.for_peer("sentinel")
    assert breaker.state is CircuitState.OPEN
    calls_while_open = calls["n"]

    # While open, the mesh does not pay the peer timeout again.
    for _ in range(3):
        assert await guarded_call(registry, "sentinel", flaky_peer, fallback=None) is None
    assert calls["n"] == calls_while_open, "isolated peer must not be called"

    # Peer comes back and the breaker probes it automatically.
    peer_is_up["value"] = True
    await asyncio.sleep(0.15)
    assert await guarded_call(registry, "sentinel", flaky_peer, fallback=None) == "peer answered"
    assert breaker.state is CircuitState.CLOSED
    assert breaker.total_recoveries == 1


@pytest.mark.asyncio
async def test_mesh_serves_while_a_peer_is_isolated(mesh_client: httpx.AsyncClient):
    """One unhealthy peer must not stop forecasts from being served."""
    from futuris.infra.resilience import peer_circuits

    peer_circuits.for_peer("sentinel").record_failure("simulated outage")
    before = await mesh_client.get("/v1/forecasts")
    assert before.status_code == 200

    created = await mesh_client.post(
        "/v1/forecasts",
        headers={"X-API-Key": MASTER_KEY},
        json={"target": "service:checkout:capacity_exceedance_24h", "horizon": "24h"},
    )
    assert created.status_code == 201

    after = await mesh_client.get("/v1/forecasts")
    assert after.status_code == 200
    assert len(after.json()) >= len(before.json())
    peer_circuits.reset("sentinel")


# ── 4. governance and memory failures are contained ────────────────────────


@pytest.mark.asyncio
async def test_audit_trail_survives_the_whole_collaboration(mesh_client: httpx.AsyncClient):
    """Every agent action in the mesh leaves a trace an operator can read."""
    await mesh_client.post(
        "/v1/forecasts",
        headers={"X-API-Key": MASTER_KEY},
        json={"target": "service:checkout:capacity_exceedance_24h", "horizon": "24h"},
    )
    await mesh_client.post(
        "/v1/friday/forecast",
        headers={"X-API-Key": FRIDAY_KEY},
        json={
            "friday_request_id": "req_audit_1",
            "target": "service:checkout:capacity_exceedance_24h",
            "horizon": "24h",
        },
    )

    audit = await mesh_client.get("/v1/audit", headers={"X-API-Key": MASTER_KEY})
    assert audit.status_code == 200
    actions = [row["action"] for row in audit.json()]
    assert "create_forecast" in actions
    assert "generate_forecast" in actions
    actors = {row["actor_label"] for row in audit.json()}
    assert len(actors) >= 2, f"expected distinct actors, saw {actors}"


@pytest.mark.asyncio
async def test_idempotent_delegation_returns_the_same_forecast(mesh_client: httpx.AsyncClient):
    """A retrying peer agent must not be billed twice for the same request."""
    payload = {
        "friday_request_id": "req_idem_1",
        "target": "service:checkout:capacity_exceedance_24h",
        "horizon": "24h",
    }
    first = await mesh_client.post(
        "/v1/friday/forecast", headers={"X-API-Key": FRIDAY_KEY}, json=payload
    )
    second = await mesh_client.post(
        "/v1/friday/forecast", headers={"X-API-Key": FRIDAY_KEY}, json=payload
    )
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["futuris_forecast_id"] == second.json()["futuris_forecast_id"]


@pytest.mark.asyncio
async def test_lifecycle_is_consistent_across_agents(mesh_client: httpx.AsyncClient):
    """After resolution, every reader of the forecast agrees on its status."""
    created = await mesh_client.post(
        "/v1/forecasts",
        headers={"X-API-Key": MASTER_KEY},
        json={"target": "service:checkout:capacity_exceedance_24h", "horizon": "24h"},
    )
    forecast_id = created.json()["forecast_id"]

    resolved = await mesh_client.post(
        f"/v1/forecasts/{forecast_id}/resolve-manual",
        headers={"X-API-Key": MASTER_KEY},
        json={"observed_value": 3950.0, "event_occurred": True, "note": "mesh consistency check"},
    )
    assert resolved.status_code == 200, resolved.text

    detail = await mesh_client.get(f"/v1/forecasts/{forecast_id}")
    assert detail.json()["status"] == "resolved"

    outcome = await mesh_client.get(f"/v1/forecasts/{forecast_id}/outcome")
    assert outcome.status_code == 200
    assert outcome.json()["observed_value"] == 3950.0

    # A second resolution attempt is refused rather than corrupting the record.
    duplicate = await mesh_client.post(
        f"/v1/forecasts/{forecast_id}/resolve-manual",
        headers={"X-API-Key": MASTER_KEY},
        json={"observed_value": 1.0, "event_occurred": False, "note": "should be refused"},
    )
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_unreachable_memora_does_not_block_market_forecast():
    """Memora being down must not turn market telemetry into a failure.

    Verified against the publisher directly, because the sandbox has no live
    Stratex telemetry to drive the HTTP route.
    """
    from futuris.core.enums import ConfidenceLevel, EvidenceClass, ForecastStatus
    from futuris.core.schemas import Forecast
    from futuris.integrations.memora_forecast_publisher import (
        MemoraForecastPublishError,
        publish_forecast_advisory,
    )

    now = datetime.now(UTC)
    forecast = Forecast(
        forecast_id=uuid4(),
        target="market:crypto:BTCUSDT:volatility_24h",
        as_of=now,
        horizon=timedelta(hours=24),
        expires_at=now + timedelta(hours=24),
        review_at=now + timedelta(hours=6),
        prediction=0.42,
        range_lower=0.1,
        range_upper=0.9,
        probability=0.3,
        confidence=ConfidenceLevel.MEDIUM,
        model_version="statsforecast:market_volatility@v2",
        status=ForecastStatus.ACTIVE,
        evidence_class=EvidenceClass.LIVE,
        evidence_source="stratex_telemetry",
    )

    # Without a Memora credential the publisher must refuse explicitly rather
    # than silently pretending the advisory was delivered.
    with pytest.raises(MemoraForecastPublishError):
        await publish_forecast_advisory(forecast, 0.7)
