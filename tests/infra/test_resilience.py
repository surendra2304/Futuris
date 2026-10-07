"""Tests for circuit breakers, bounded degradation and the self-healing supervisor."""

import asyncio

import httpx
import pytest
from sqlalchemy import text

from futuris.infra.resilience import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitState,
    guarded_call,
    peer_circuits,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ── breaker state machine ──────────────────────────────────────────────────


def test_breaker_opens_after_threshold_failures():
    clock = FakeClock()
    breaker = CircuitBreaker("peer", failure_threshold=3, recovery_timeout=30.0, clock=clock)
    assert breaker.state is CircuitState.CLOSED

    for _ in range(2):
        breaker.record_failure("boom")
    assert breaker.state is CircuitState.CLOSED, "opens only at the threshold"

    breaker.record_failure("boom")
    assert breaker.state is CircuitState.OPEN
    assert breaker.allow_request() is False


def test_open_circuit_refuses_until_cooldown_then_probes():
    clock = FakeClock()
    breaker = CircuitBreaker("peer", failure_threshold=1, recovery_timeout=30.0, clock=clock)
    breaker.record_failure("down")
    assert breaker.allow_request() is False

    clock.advance(29.9)
    assert breaker.allow_request() is False, "still inside the cooldown"

    clock.advance(0.2)
    assert breaker.allow_request() is True, "a probe is allowed after cooldown"
    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.allow_request() is False, "only one probe at a time"


def test_successful_probe_closes_the_circuit_and_is_counted():
    clock = FakeClock()
    breaker = CircuitBreaker("peer", failure_threshold=1, recovery_timeout=10.0, clock=clock)
    breaker.record_failure("down")
    clock.advance(11)
    breaker.allow_request()
    breaker.record_success()

    assert breaker.state is CircuitState.CLOSED
    assert breaker.total_recoveries == 1
    assert breaker.consecutive_failures == 0
    assert breaker.snapshot()["last_error"] is None


def test_failed_probe_reopens_the_circuit():
    clock = FakeClock()
    breaker = CircuitBreaker("peer", failure_threshold=1, recovery_timeout=10.0, clock=clock)
    breaker.record_failure("down")
    clock.advance(11)
    breaker.allow_request()
    breaker.record_failure("still down")

    assert breaker.state is CircuitState.OPEN
    assert breaker.snapshot()["retry_in_seconds"] == 10.0


def test_registry_tracks_and_resets_peers():
    registry = CircuitBreakerRegistry(failure_threshold=1)
    for name in ("alpha", "beta"):
        registry.for_peer(name).record_failure("x")
    assert registry.unhealthy() == ["alpha", "beta"]

    registry.reset("alpha")
    assert registry.unhealthy() == ["beta"]
    assert [s["peer"] for s in registry.snapshots()] == ["alpha", "beta"]


# ── guarded calls ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guarded_call_returns_fallback_instead_of_raising():
    registry = CircuitBreakerRegistry(failure_threshold=2)

    async def failing() -> str:
        raise httpx.ConnectError("no route to host")

    result = await guarded_call(registry, "peer", failing, fallback="degraded")
    assert result == "degraded"
    assert registry.for_peer("peer").consecutive_failures == 1


@pytest.mark.asyncio
async def test_guarded_call_short_circuits_after_threshold():
    registry = CircuitBreakerRegistry(failure_threshold=2)
    calls = {"n": 0}

    async def failing() -> None:
        calls["n"] += 1
        raise httpx.ConnectError("down")

    for _ in range(5):
        await guarded_call(registry, "peer", failing, fallback=None)

    assert calls["n"] == 2, "later calls must not touch the network"
    assert registry.for_peer("peer").total_short_circuits == 3


@pytest.mark.asyncio
async def test_guarded_call_is_transparent_when_healthy():
    registry = CircuitBreakerRegistry()

    async def ok() -> str:
        return "value"

    assert await guarded_call(registry, "peer", ok) == "value"
    breaker = registry.for_peer("peer")
    assert breaker.state is CircuitState.CLOSED
    assert breaker.total_calls == 1


@pytest.mark.asyncio
async def test_peer_probe_does_not_hang_when_peer_is_unreachable():
    """A dead peer must not cost more than the probe timeout."""
    from futuris.ecosystem.adapters import EcosystemAdapter

    started = asyncio.get_event_loop().time()
    probes = await EcosystemAdapter(timeout_seconds=0.2).probe_peers()
    elapsed = asyncio.get_event_loop().time() - started

    assert probes, "the probe must always return a report"
    assert elapsed < 15.0, f"probe pass took {elapsed:.1f}s"
    for probe in probes:
        assert probe["status"] in {"online", "offline", "degraded", "isolated"}


# ── self-healing supervisor ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_report_is_measured_not_assumed(tmp_path, monkeypatch):
    from futuris.infra import self_healing
    from futuris.infra.self_healing import SelfHealingSupervisor

    supervisor = SelfHealingSupervisor()
    report = await supervisor.status()

    assert report["status"] in {"ok", "degraded", "down", "unknown"}
    assert report["evidence_class"] == "live"
    names = {s["subsystem"] for s in report["subsystems"]}
    assert {"database", "schema", "evidence_integrity", "scheduler", "peer_mesh"} <= names
    for subsystem in report["subsystems"]:
        assert subsystem["state"] in self_healing.HEALTH_STATES
        assert subsystem["measured_at"]

    # The scheduler is not running under pytest; the report must say so
    # rather than claiming everything is fine.
    scheduler = next(s for s in report["subsystems"] if s["subsystem"] == "scheduler")
    assert scheduler["state"] in {"down", "unconfigured"}


@pytest.mark.asyncio
async def test_supervisor_reports_database_failure_honestly():
    from futuris.infra.self_healing import SelfHealingSupervisor

    supervisor = SelfHealingSupervisor()
    check = await supervisor.check_database()
    assert check.state == "ok"
    assert check.evidence_class == "live"

    # Point the check at an impossible database and confirm it reports down
    # instead of raising or claiming health.
    from sqlalchemy.ext.asyncio import create_async_engine

    from futuris.infra import self_healing as module

    original = module.engine
    module.engine = create_async_engine("sqlite+aiosqlite:////nonexistent-dir/x.db")
    try:
        broken = await supervisor.check_database()
    finally:
        module.engine = original

    assert broken.state == "down"
    assert "OperationalError" in broken.detail or "unable to open" in broken.detail


@pytest.mark.asyncio
async def test_healing_records_actions_and_is_idempotent():
    from futuris.infra.self_healing import SelfHealingSupervisor

    supervisor = SelfHealingSupervisor()
    actions = await supervisor.heal(await supervisor.status_tables_ok())
    assert actions == [], "nothing broken means nothing to heal"

    from futuris.infra.self_healing import SubsystemCheck

    broken = [SubsystemCheck("database", "down", "simulated", "live")]
    healed = await supervisor.heal(broken)
    assert any(a["action"] == "ensure_storage_directories" for a in healed)
    assert supervisor.healing_history


@pytest.mark.asyncio
async def test_status_endpoint_requires_authentication():
    from futuris.api.app import app
    from futuris.infra.config import settings

    original = settings.FUTURIS_API_KEY
    settings.FUTURIS_API_KEY = "self_status_test_key_0123456789abcdef"
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            anon = await client.get("/v1/self/status")
            assert anon.status_code in (401, 403)

            authed = await client.get(
                "/v1/self/status",
                headers={"X-API-Key": "self_status_test_key_0123456789abcdef"},
            )
            assert authed.status_code == 200, authed.text
            body = authed.json()
            assert body["status"] in {"ok", "degraded", "down", "unknown"}
            assert body["subsystems"]

            caps = await client.get(
                "/v1/self/capabilities",
                headers={"X-API-Key": "self_status_test_key_0123456789abcdef"},
            )
            assert caps.status_code == 200
            capabilities = caps.json()["capabilities"]
            assert any(c["capability"] == "statistical_forecasting" for c in capabilities)
            for entry in capabilities:
                assert entry["state"] in {"live", "unconfigured", "degraded", "down"}
    finally:
        settings.FUTURIS_API_KEY = original
        peer_circuits._breakers.clear()


@pytest.mark.asyncio
async def test_database_round_trip_check_uses_the_real_engine():
    from futuris.infra.self_healing import SelfHealingSupervisor

    check = await SelfHealingSupervisor().check_database()
    assert check.state == "ok"
    assert check.data["dialect"] == "sqlite"

    from futuris.storage.db import engine

    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT 1"))).scalar() == 1
