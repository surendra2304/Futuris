"""Round-2 hardening tests.

Each test pins one behaviour changed in round 2:

* anonymous reads draw on a per-client budget and get a 429 with Retry-After
  (S04/S05); authenticated callers are not charged; X-Forwarded-For is trusted
  only when TRUST_PROXY_HEADERS is set;
* baseline security headers, a CSP scoped to the console, HSTS only in
  production (S12);
* OpenAPI and Swagger are off in production by default (S11); alias mounts are
  hidden from the schema but still routed (M12);
* the write-capable paths and the self-healing controls leave audit rows (S05, S20);
* lifecycle expiry records why a resolution failed (R12);
* skipped status checks do not leak un-awaited coroutines;
* evaluation run identifiers are random UUIDs (R9);
* every test runs against its own database (D13).
"""

from __future__ import annotations

import gc
import warnings
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pandas as pd
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select

from futuris.api.app import _docs_enabled, app, iter_route_paths
from futuris.api.errors import register_error_handlers
from futuris.api.routers import predictions as predictions_router
from futuris.core.enums import ForecastEventType, ForecastStatus
from futuris.core.lifecycle import LifecycleManager
from futuris.core.universe_domains import UNIVERSE_TARGETS
from futuris.infra import self_healing
from futuris.infra.auth import (
    AllowAnonymousHeavyRead,
    AllowAnonymousRead,
    client_address,
)
from futuris.infra.config import settings
from futuris.infra.resilience import peer_circuits
from futuris.storage import db as storage_db
from futuris.storage.models import AuditLogModel
from futuris.storage.repositories import EvaluationRepository

MASTER_KEY = "round2_master_key_0123456789abcdef0123"


@pytest.fixture
def master_key(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "FUTURIS_API_KEY", MASTER_KEY)
    return MASTER_KEY


@pytest_asyncio.fixture
async def api() -> AsyncGenerator[httpx.AsyncClient, None]:
    """In-process client for the real application (same event loop as the test)."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


def _probe_app() -> FastAPI:
    """A minimal app with one route per budget class, so budgets are tested in isolation."""
    probe = FastAPI()

    @probe.get("/heavy")
    async def heavy(user: AllowAnonymousHeavyRead) -> dict[str, str]:
        return {"label": user.label}

    @probe.get("/light")
    async def light(user: AllowAnonymousRead) -> dict[str, str]:
        return {"label": user.label}

    @probe.get("/whoami")
    async def whoami(request: Request) -> dict[str, str]:
        return {"address": client_address(request)}

    register_error_handlers(probe)
    return probe


async def _audit_rows(action: str) -> list[AuditLogModel]:
    async with storage_db.async_session_factory() as session:
        result = await session.execute(select(AuditLogModel).where(AuditLogModel.action == action))
        return list(result.scalars())


# ── anonymous request budgets (S04 / S05) ─────────────────────────────────────


def test_anonymous_heavy_reads_are_budgeted_with_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ANONYMOUS_HEAVY_READ_RATE_LIMIT_PER_MINUTE", 2)
    with TestClient(_probe_app()) as tc:
        assert tc.get("/heavy").status_code == 200
        assert tc.get("/heavy").status_code == 200
        blocked = tc.get("/heavy")
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
    error = blocked.json()["error"]
    assert error["code"] == "rate_limited"
    assert "Retry after" in error["message"]


def test_authenticated_callers_are_not_charged_against_the_anonymous_budget(
    monkeypatch: pytest.MonkeyPatch, master_key: str
) -> None:
    monkeypatch.setattr(settings, "ANONYMOUS_HEAVY_READ_RATE_LIMIT_PER_MINUTE", 1)
    with TestClient(_probe_app()) as tc:
        for _ in range(5):
            response = tc.get("/heavy", headers={"X-API-Key": master_key})
            assert response.status_code == 200
            assert response.json()["label"] == "master_admin"
        # The anonymous budget was never touched, so one anonymous call still fits.
        assert tc.get("/heavy").status_code == 200


def test_heavy_and_light_anonymous_budgets_are_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ANONYMOUS_HEAVY_READ_RATE_LIMIT_PER_MINUTE", 1)
    monkeypatch.setattr(settings, "ANONYMOUS_READ_RATE_LIMIT_PER_MINUTE", 5)
    with TestClient(_probe_app()) as tc:
        assert tc.get("/heavy").status_code == 200
        assert tc.get("/heavy").status_code == 429
        assert tc.get("/light").status_code == 200


def test_forwarded_for_is_ignored_unless_proxy_trust_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    with TestClient(_probe_app()) as tc:
        response = tc.get("/whoami", headers={"X-Forwarded-For": "203.0.113.9"})
    assert response.json()["address"] != "203.0.113.9"


def test_trusted_proxy_uses_the_rightmost_forwarded_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The leftmost entry is client-controlled; the proxy appends the real address last."""
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    with TestClient(_probe_app()) as tc:
        response = tc.get("/whoami", headers={"X-Forwarded-For": "203.0.113.66, 198.51.100.7"})
    assert response.json()["address"] == "198.51.100.7"


def test_trusted_proxy_gives_each_forwarded_client_its_own_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "ANONYMOUS_HEAVY_READ_RATE_LIMIT_PER_MINUTE", 1)
    with TestClient(_probe_app()) as tc:
        first = {"X-Forwarded-For": "198.51.100.1"}
        assert tc.get("/heavy", headers=first).status_code == 200
        assert tc.get("/heavy", headers=first).status_code == 429
        other = {"X-Forwarded-For": "198.51.100.2"}
        assert tc.get("/heavy", headers=other).status_code == 200


# ── security headers and documentation surface (S11 / S12) ─────────────────────


async def test_api_responses_carry_baseline_security_headers(api: httpx.AsyncClient) -> None:
    response = await api.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "content-security-policy" not in response.headers
    assert "strict-transport-security" not in response.headers  # not production


async def test_console_gets_a_strict_content_security_policy(api: httpx.AsyncClient) -> None:
    response = await api.get("/ui/")
    assert response.status_code == 200
    policy = response.headers["content-security-policy"]
    assert "script-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "unsafe-eval" not in policy


async def test_hsts_is_sent_only_in_production(
    api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    response = await api.get("/health")
    assert response.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )


def test_docs_default_follows_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DOCS_ENABLED", None)
    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert _docs_enabled() is False
    monkeypatch.setattr(settings, "APP_ENV", "dev")
    assert _docs_enabled() is True
    monkeypatch.setattr(settings, "DOCS_ENABLED", False)
    assert _docs_enabled() is False
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DOCS_ENABLED", True)
    assert _docs_enabled() is True


def test_openapi_documents_each_operation_once_and_alias_paths_stay_routed() -> None:
    schema = app.openapi()
    operation_ids = [
        operation["operationId"]
        for path_item in schema["paths"].values()
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "patch", "delete"} and "operationId" in operation
    ]
    assert len(operation_ids) == len(set(operation_ids))
    assert not [p for p in schema["paths"] if p.startswith(("/api/", "/v1/market/"))]
    routed = iter_route_paths(app)
    assert {"/api/v1/futuris/forecast", "/v1/market/forecast", "/api/v1/task/execute"} <= routed


# ── audit trail for write-capable and self-healing actions (S05 / S20) ─────────


async def test_self_heal_pass_is_audited(api: httpx.AsyncClient, master_key: str) -> None:
    response = await api.post("/v1/self/heal", headers={"X-API-Key": master_key})
    assert response.status_code == 200
    rows = await _audit_rows("self_heal_pass")
    assert rows
    assert rows[-1].actor_label == "master_admin"
    assert rows[-1].entity == "self_healing"


async def test_peer_circuit_reset_is_audited(api: httpx.AsyncClient, master_key: str) -> None:
    peer = f"r2-peer-{uuid4().hex[:8]}"
    peer_circuits.for_peer(peer)
    try:
        response = await api.post(
            f"/v1/self/peers/{peer}/reset", headers={"X-API-Key": master_key}
        )
    finally:
        peer_circuits._breakers.pop(peer, None)
    assert response.status_code == 200
    rows = await _audit_rows("peer_circuit_reset")
    assert [row.entity_id for row in rows] == [peer]


async def test_anonymous_matrix_backfill_is_audited_as_anonymous_read(
    api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def backfill_one_target(session, present, *, budget_seconds=0.0):
        # A None entry marks the target as backfilled without building a forecast;
        # the matrix assembly skips it, but the audit must still record the write.
        target = next(iter(UNIVERSE_TARGETS))
        present[target] = None
        return 1

    monkeypatch.setattr(predictions_router, "refresh_missing_within_budget", backfill_one_target)
    response = await api.get("/v1/predictions/matrix")
    assert response.status_code == 200
    rows = await _audit_rows("matrix_backfill")
    assert len(rows) == 1
    assert rows[0].actor_label == "anonymous_read"
    assert rows[0].entity == "universe_matrix"


async def test_matrix_read_without_backfill_writes_no_audit_row(
    api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def nothing_to_backfill(session, present, *, budget_seconds=0.0):
        return 0

    monkeypatch.setattr(predictions_router, "refresh_missing_within_budget", nothing_to_backfill)
    response = await api.get("/v1/predictions/matrix")
    assert response.status_code == 200
    assert await _audit_rows("matrix_backfill") == []


async def test_refresh_all_is_audited_with_the_analyst_as_actor(
    api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, master_key: str
) -> None:
    async def refresh_nothing(session, *, budget_seconds=0.0):
        return 0

    async def backfill_nothing(session, present, *, budget_seconds=0.0):
        return 0

    monkeypatch.setattr(predictions_router, "refresh_all_within_budget", refresh_nothing)
    monkeypatch.setattr(predictions_router, "refresh_missing_within_budget", backfill_nothing)
    response = await api.post("/v1/predictions/refresh-all", headers={"X-API-Key": master_key})
    assert response.status_code == 200
    rows = await _audit_rows("refresh_all")
    assert len(rows) == 1
    assert rows[0].actor_label == "master_admin"


# ── lifecycle expiry records its reason (R12) ──────────────────────────────────


class _ForecastRepoFake:
    def __init__(self, forecasts: list[SimpleNamespace]) -> None:
        self._forecasts = forecasts
        self.status_updates: list[tuple[object, ForecastStatus]] = []

    async def list_by_status(self, status: ForecastStatus) -> list[SimpleNamespace]:
        return list(self._forecasts) if status == ForecastStatus.ACTIVE else []

    async def update_status(self, forecast_id: object, status: ForecastStatus) -> None:
        self.status_updates.append((forecast_id, status))


class _EventRepoFake:
    def __init__(self) -> None:
        self.events: list = []

    async def append(self, event) -> None:
        self.events.append(event)


class _RaisingResolver:
    def resolve_forecast(self, forecast, observations_df):
        raise LookupError("no ground-truth observations for the forecast horizon")


class _SilentEmitter:
    async def emit(self, event) -> None:
        return None


async def test_expiry_after_failed_resolution_records_the_reason() -> None:
    now = datetime.now(UTC)
    forecast = SimpleNamespace(
        forecast_id=uuid4(),
        as_of=now - timedelta(days=2),
        expires_at=now - timedelta(hours=1),
        review_at=now - timedelta(days=1),
    )
    forecasts = _ForecastRepoFake([forecast])
    events = _EventRepoFake()
    manager = LifecycleManager(
        forecast_repo=forecasts,  # type: ignore[arg-type]
        outcome_repo=None,  # type: ignore[arg-type]
        event_repo=events,  # type: ignore[arg-type]
        resolver=_RaisingResolver(),  # type: ignore[arg-type]
        emitter=_SilentEmitter(),  # type: ignore[arg-type]
    )

    report = await manager.run_lifecycle_sweep(pd.DataFrame(), as_of=now)

    assert report.expired_count == 1
    assert report.resolution_failures == 1
    assert forecasts.status_updates == [(forecast.forecast_id, ForecastStatus.EXPIRED)]
    assert len(events.events) == 1
    event = events.events[0]
    assert event.event_type == ForecastEventType.FORECAST_RESOLUTION_FAILED
    assert event.forecast_id == forecast.forecast_id
    assert event.payload["error_type"] == "LookupError"
    assert "ground-truth" in event.payload["error"]


# ── coroutine hygiene in the status budget (the RuntimeWarning) ───────────────


async def test_status_checks_skipped_for_budget_do_not_leak_coroutines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(self_healing, "STATUS_MEASUREMENT_BUDGET_SECONDS", 0.0)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await self_healing.self_healing_supervisor._measure()
        gc.collect()
    leaked = [w for w in caught if "never awaited" in str(w.message)]
    assert leaked == []


# ── run identifiers and test isolation (R9 / D13) ─────────────────────────────


async def test_evaluation_run_ids_are_random_uuid4(ready_storage) -> None:
    from futuris.core.schemas import ModelInfo
    from futuris.storage.repositories import ModelRepository

    async with storage_db.async_session_factory() as session:
        # evaluation_runs.model_version is a foreign key: the model must exist first.
        await ModelRepository(session).register(
            ModelInfo(
                model_version="r2_run_id_model:v1",
                family="baseline.historic_mean",
                config_hash="r2_hash",
                promoted_at=datetime.now(UTC),
                benchmark_scores={"crps": 0.05},
            )
        )
        repo = EvaluationRepository(session)
        run_ids = [
            await repo.save_run("r2_run_id_model:v1", "dataset-y", {"brier": 0.1})
            for _ in range(3)
        ]
        await session.commit()
    assert all(isinstance(run_id, UUID) and run_id.version == 4 for run_id in run_ids)
    assert len(set(run_ids)) == len(run_ids)


def test_every_test_runs_against_its_own_database() -> None:
    """Guards D13: the default engine must never be the developer's workspace file."""
    assert "data/futuris.db" not in str(storage_db.engine.url)
