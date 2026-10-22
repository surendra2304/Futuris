"""Live-fire regression tests: bugs found by driving a running server hard.

Each test is named after the failure it pins. The failures were found with
``scripts/extreme_pressure_harness.py`` and ``scripts/pressure_harness.py``
against a real uvicorn under concurrent load, not by reading code:

* concurrent FRIDAY delegations with one idempotency key created N forecasts
  (check-then-create race; the unique index + replay-on-conflict fix it);
* the same idempotency key with a different payload silently served the wrong
  cached forecast;
* invalidating a RESOLVED forecast (or resolving an INVALIDATED one) succeeded,
  leaving the outcome row and the forecast status in disagreement;
* an empty forecast target passed request validation and 500'd deep inside
  the engine; an oversized target was accepted;
* non-numeric ``context`` values were silently ignored by /v1/forecasts;
* an unregistered universe target got a fabricated spec and a forecast;
* percent-scale universe risk compared 0-100 predictions against 0-1
  thresholds (everything CRITICAL) and rendered "0.0%" interpretations;
* non-pipeline universe targets were served demand-model numbers relabelled as
  failure rates / health indices;
* intervals for non-negative series had deeply negative lower bounds;
* the forecast list loaded every row and paginated in Python;
* scenario runs, webhook subscribe/delete and demo-seed triggers were not
  audit-logged although SECURITY.md says every mutation is;
* /v1/self/status reported the scheduler as down while it was running and the
  agent surface as broken on current FastAPI (lazy included routers);
* the unhandled-error handler returned the raw exception text to clients;
* an explicit ``POST /v1/self/heal`` served the two-second liveness cache
  instead of running a pass, so a table dropped inside the cache window
  stayed missing while the response replayed the previous pass's actions.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from futuris.api.app import app, iter_route_paths
from futuris.api.deps import get_db_session
from futuris.core.enums import ForecastStatus
from futuris.core.schemas import Forecast
from futuris.core.universe_domains import evaluate_risk_level, get_target_spec
from futuris.models.adapters import NaiveAdapter, SeasonalNaiveAdapter
from futuris.storage.models import Base
from futuris.storage.repositories import ForecastRepository

MASTER_KEY = "live_fire_test_key_0123456789abcdef"
FRIDAY_KEY = "live_fire_friday_key_0123456789abcdef"


@pytest_asyncio.fixture
async def client(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """ASGI client with an isolated file-backed database and both keys set.

    A *file* database (not ``:memory:``) with ``NullPool`` and the production
    SQLite pragmas is deliberate: every request gets its own connection to the
    same database, so concurrent requests exercise the real concurrency
    behaviour -- including the unique idempotency index -- instead of each
    seeing a private empty memory database. The override commits at teardown
    exactly like the real ``get_db_session`` dependency.
    """
    from sqlalchemy import pool

    from futuris.infra.config import settings
    from futuris.storage.db import install_sqlite_pragmas

    monkeypatch.setattr(settings, "FUTURIS_API_KEY", MASTER_KEY)
    monkeypatch.setattr(settings, "FUTURIS_FRIDAY_API_KEY", FRIDAY_KEY)
    monkeypatch.setattr(settings, "API_KEYS_ENABLED", True)

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'live_fire.db'}", poolclass=pool.NullPool
    )
    install_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


MASTER = {"X-API-Key": MASTER_KEY}
FRIDAY = {"X-API-Key": FRIDAY_KEY}


async def _create_forecast(client: httpx.AsyncClient, target: str = "service:t:capacity_24h"):
    resp = await client.post(
        "/v1/forecasts", headers=MASTER, json={"target": target, "horizon": "1h"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["forecast_id"]


# ── idempotency races ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_delegation_with_one_key_creates_one_forecast(
    client: httpx.AsyncClient,
):
    """A retry storm with the same friday_request_id must create ONE forecast."""
    request_id = f"req_race_{uuid4().hex[:8]}"

    async def delegate() -> httpx.Response:
        return await client.post(
            "/v1/friday/forecast",
            headers=FRIDAY,
            json={
                "friday_request_id": request_id,
                "target": "service:checkout:capacity_exceedance_24h",
                "horizon": "1h",
            },
        )

    responses = await asyncio.gather(*[delegate() for _ in range(6)])
    assert all(r.status_code == 201 for r in responses), [r.status_code for r in responses]
    ids = {r.json()["futuris_forecast_id"] for r in responses}
    assert len(ids) == 1, f"retry storm created {len(ids)} forecasts: {ids}"


@pytest.mark.asyncio
async def test_idempotency_key_reuse_with_different_payload_conflicts(
    client: httpx.AsyncClient,
):
    """Same key, different target: a loud 409, never the wrong cached forecast."""
    request_id = f"req_conflict_{uuid4().hex[:8]}"
    first = await client.post(
        "/v1/friday/forecast",
        headers=FRIDAY,
        json={
            "friday_request_id": request_id,
            "target": "service:checkout:capacity_exceedance_24h",
            "horizon": "1h",
        },
    )
    assert first.status_code == 201
    second = await client.post(
        "/v1/friday/forecast",
        headers=FRIDAY,
        json={
            "friday_request_id": request_id,
            "target": "service:payments:latency_p95_24h",
            "horizon": "1h",
        },
    )
    assert second.status_code == 409, second.text


# ── lifecycle integrity ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalidate_resolved_forecast_is_refused(client: httpx.AsyncClient):
    """A resolved forecast keeps its outcome; invalidating it is a 409."""
    fid = await _create_forecast(client)
    resolved = await client.post(
        f"/v1/forecasts/{fid}/resolve-manual",
        headers=MASTER,
        json={"observed_value": 3900.0, "event_occurred": False, "note": "operator resolution"},
    )
    assert resolved.status_code == 200
    invalidated = await client.post(
        f"/v1/forecasts/{fid}/invalidate",
        headers=MASTER,
        json={"reason": "attempt to invalidate a resolved forecast"},
    )
    assert invalidated.status_code == 409, invalidated.text
    detail = await client.get(f"/v1/forecasts/{fid}", headers=MASTER)
    assert detail.json()["status"] == "resolved"


@pytest.mark.asyncio
async def test_resolve_invalidated_forecast_is_refused(client: httpx.AsyncClient):
    """An invalidated forecast cannot be resolved back to life."""
    fid = await _create_forecast(client)
    invalidated = await client.post(
        f"/v1/forecasts/{fid}/invalidate",
        headers=MASTER,
        json={"reason": "assumption broken in test"},
    )
    assert invalidated.status_code == 200
    resolved = await client.post(
        f"/v1/forecasts/{fid}/resolve-manual",
        headers=MASTER,
        json={"observed_value": 1.0, "event_occurred": False, "note": "resolve after invalidate"},
    )
    assert resolved.status_code == 409, resolved.text


@pytest.mark.asyncio
async def test_cancel_resolved_forecast_is_refused(client: httpx.AsyncClient):
    """Cancellation is a lifecycle transition, not an overwrite."""
    fid = await _create_forecast(client)
    await client.post(
        f"/v1/forecasts/{fid}/resolve-manual",
        headers=MASTER,
        json={"observed_value": 3900.0, "event_occurred": False, "note": "resolve first"},
    )
    cancelled = await client.post(f"/v1/friday/forecasts/{fid}/cancel", headers=FRIDAY)
    assert cancelled.status_code == 409, cancelled.text


@pytest.mark.asyncio
async def test_concurrent_manual_resolution_records_exactly_one_outcome(
    client: httpx.AsyncClient,
):
    """Six racing resolutions: one 200, the rest 409, one outcome row."""
    fid = await _create_forecast(client)

    async def resolve(i: int) -> httpx.Response:
        return await client.post(
            f"/v1/forecasts/{fid}/resolve-manual",
            headers=MASTER,
            json={
                "observed_value": 3900.0 + i,
                "event_occurred": False,
                "note": f"racing resolution {i}",
            },
        )

    responses = await asyncio.gather(*[resolve(i) for i in range(6)])
    statuses = sorted(r.status_code for r in responses)
    assert statuses.count(200) == 1
    assert set(statuses) <= {200, 409}
    outcomes = await client.get(f"/v1/forecasts/{fid}/outcome", headers=MASTER)
    assert outcomes.status_code == 200


# ── request validation ───────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"target": "", "horizon": "1h"},
        {"target": "   ", "horizon": "1h"},
        {"target": "x" * 256, "horizon": "1h"},
        {"target": "t", "horizon": "1h", "context": {"point_estimate": "not-a-number"}},
        {"target": "t", "horizon": "1h", "context": {"probability": 1.5}},
    ],
)
async def test_invalid_forecast_requests_are_422(
    client: httpx.AsyncClient, payload: dict
):
    """Empty/oversized targets and non-numeric context are 422, never 500."""
    resp = await client.post("/v1/forecasts", headers=MASTER, json=payload)
    assert resp.status_code == 422, f"{payload} -> {resp.status_code}: {resp.text[:200]}"
    assert resp.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_unknown_universe_target_is_422(client: httpx.AsyncClient):
    """A typo'd universe target must not get a fabricated spec and a forecast."""
    resp = await client.post(
        "/v1/predictions/predict",
        headers=MASTER,
        json={"target": "forge:ci_cd:typo_target_24h"},
    )
    assert resp.status_code == 422, resp.text


# ── universe honesty ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_pipeline_target_without_context_is_insufficient_data(
    client: httpx.AsyncClient,
):
    """No telemetry source for a failure-rate target -> honest insufficient_data.

    The demand pipeline must not be relabelled as a CI failure rate.
    """
    resp = await client.post(
        "/v1/predictions/predict",
        headers=MASTER,
        json={"target": "forge:ci_cd:pipeline_failure_risk_24h"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "insufficient_data"
    assert body["evidence_class"] == "synthetic"


def test_percent_scale_risk_normalisation():
    """A 0-100 caller estimate must be compared on the 0-1 threshold scale."""
    spec = get_target_spec("sentinel:security:threat_anomaly_risk_24h")
    # 42% with no probability: was classified CRITICAL against a 0.60 threshold.
    assert evaluate_risk_level(spec, prediction=42.0, probability=None).value == "HIGH"
    # 5% is genuinely nominal.
    assert evaluate_risk_level(spec, prediction=5.0, probability=None).value == "NOMINAL"
    # A real probability still wins.
    assert evaluate_risk_level(spec, prediction=42.0, probability=0.9).value == "CRITICAL"


# ── interval sanity ──────────────────────────────────────────────────────────


def test_intervals_are_clamped_at_zero_for_non_negative_series():
    """A demand forecast must never carry a negative lower bound."""
    import numpy as np
    import pandas as pd

    y = pd.Series(np.abs(np.random.default_rng(1).normal(1200.0, 300.0, 400)) + 50.0)
    x = pd.DataFrame({"f": np.zeros(len(y))})
    adapter = SeasonalNaiveAdapter(season_length=24)
    adapter.fit(x, y, as_of=datetime.now(UTC))
    pred = adapter.predict(horizon=24, capacity_threshold=4000.0)
    assert pred.range_lower >= 0.0, f"negative lower bound: {pred.range_lower}"
    for interval in pred.intervals:
        assert interval.lower >= 0.0


def test_intervals_not_clamped_for_series_with_negatives():
    """A series that legitimately goes negative keeps its negative bounds."""
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(2)
    y = pd.Series(rng.normal(0.0, 50.0, 400))  # mean ~0, plenty of negatives
    x = pd.DataFrame({"f": np.zeros(len(y))})
    adapter = NaiveAdapter()
    adapter.fit(x, y, as_of=datetime.now(UTC))
    pred = adapter.predict(horizon=12)
    assert pred.range_lower < 0.0


# ── list pagination ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_forecasts_paginates_in_sql(client: httpx.AsyncClient):
    """limit/offset bound the rows returned and X-Total-Count is the full count."""
    for i in range(5):
        await _create_forecast(client, target=f"service:t{i}:capacity_24h")
    page = await client.get("/v1/forecasts", headers=MASTER, params={"limit": 2, "offset": 0})
    assert page.status_code == 200
    assert len(page.json()) == 2
    assert page.headers["X-Total-Count"] == "5"
    page2 = await client.get("/v1/forecasts", headers=MASTER, params={"limit": 2, "offset": 2})
    assert len(page2.json()) == 2
    assert page2.headers["X-Total-Count"] == "5"
    ids = {f["forecast_id"] for f in page.json()} | {f["forecast_id"] for f in page2.json()}
    assert len(ids) == 4


# ── audit completeness ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario_run_is_audit_logged(client: httpx.AsyncClient):
    """Scenario execution is a mutation of platform state and must be audited."""
    fid = await _create_forecast(client)
    resp = await client.post(
        f"/v1/forecasts/{fid}/scenarios",
        headers=MASTER,
        json={
            "scenarios": [
                {
                    "scenario_type": "stress",
                    "name": "audit-test",
                    "assumption_overrides": {"demand": 1.4, "capacity": 0.8},
                    "rationale": "audit coverage",
                }
            ],
            "use_monte_carlo": False,
        },
    )
    assert resp.status_code == 200, resp.text
    audit = await client.get("/v1/audit", headers=MASTER)
    actions = [row["action"] for row in audit.json()]
    assert "run_scenarios" in actions


@pytest.mark.asyncio
async def test_webhook_subscribe_and_delete_are_audit_logged(client: httpx.AsyncClient):
    """Webhook management is a mutation and must be audited."""
    created = await client.post(
        "/v1/webhooks",
        headers=MASTER,
        json={"url": "https://example.com/audit-test-hook"},
    )
    assert created.status_code == 201
    sub_id = created.json()["subscription_id"]
    deleted = await client.delete(f"/v1/webhooks/{sub_id}", headers=MASTER)
    assert deleted.status_code == 204
    audit = await client.get("/v1/audit", headers=MASTER)
    actions = [row["action"] for row in audit.json()]
    assert "create_webhook" in actions
    assert "delete_webhook" in actions


# ── self-model truth ─────────────────────────────────────────────────────────


def test_iter_route_paths_includes_router_mounted_routes():
    """The route walker must see routes behind FastAPI's lazy included routers."""
    paths = iter_route_paths(app)
    assert "/v1/forecasts" in paths
    assert "/v1/friday/forecast" in paths
    assert "/v1/self/status" in paths
    assert "/v1/task/execute" in paths
    assert len(paths) > 40


# ── error envelope hygiene ───────────────────────────────────────────────────


def test_unhandled_error_envelope_does_not_leak_exception_text():
    """The 500 envelope names the error type, never the driver's message."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from futuris.api.errors import register_error_handlers

    probe = FastAPI()

    @probe.get("/boom")
    async def boom() -> None:
        raise RuntimeError("sqlite3.OperationalError: no such table: /var/lib/secrets.db")

    register_error_handlers(probe)
    with TestClient(probe, raise_server_exceptions=False) as tc:
        resp = tc.get("/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "internal_server_error"
    assert "secrets.db" not in resp.text
    assert "no such table" not in resp.text
    assert body["error"]["details"]["error_type"] == "RuntimeError"


# ── repository transition guard ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transition_status_refuses_terminal_states(client: httpx.AsyncClient):
    """The guarded transition is the repository-level backstop for the 409s."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        repo = ForecastRepository(session)
        forecast = Forecast(
            target="service:t:capacity_24h",
            as_of=datetime.now(UTC),
            horizon=timedelta(hours=1),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            prediction=100.0,
            range_lower=50.0,
            range_upper=150.0,
            confidence="low",
            model_version="naive@v1:test",
            review_at=datetime.now(UTC) + timedelta(minutes=15),
            status=ForecastStatus.RESOLVED,
        )
        await repo.create(forecast)
        moved = await repo.transition_status(
            forecast.forecast_id,
            {ForecastStatus.ACTIVE, ForecastStatus.DRAFT},
            ForecastStatus.INVALIDATED,
        )
        assert moved is None, "terminal state must refuse transition"
        still = await repo.get(forecast.forecast_id)
        assert still is not None and still.status == ForecastStatus.RESOLVED
    await engine.dispose()


# ── explicit heal bypasses the liveness cache (B19) ──────────────────────────


@pytest.mark.asyncio
async def test_explicit_heal_bypasses_the_status_cache(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path
):
    """POST /v1/self/heal must measure and heal NOW, never replay the cache.

    The status payload is cached for two seconds so the mesh can poll it
    cheaply; an explicit healing pass is an operator action and must run a
    real pass even inside the cache window. Found by the full-suite run: the
    heal returned 200 with the previous pass's actions while the dropped
    table stayed missing.
    """
    from sqlalchemy import pool, text

    from futuris.infra import self_healing as self_healing_module

    # A second engine on the fixture's database file, so the supervisor can
    # be pointed at the same schema the app serves.
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'live_fire.db'}", poolclass=pool.NullPool
    )
    monkeypatch.setattr(self_healing_module, "engine", engine)
    try:
        # Warm the liveness cache with a healthy status.
        warm = await client.get("/v1/self/status", headers=MASTER)
        assert warm.status_code == 200, warm.text

        # Break the schema underneath the warm cache.
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE intelx_notices"))

        # The polled GET may legitimately serve the warm cache ...
        polled = await client.get("/v1/self/status", headers=MASTER)
        assert polled.status_code == 200, polled.text

        # ... but an explicit heal must run a fresh pass and repair it.
        heal = await client.post("/v1/self/heal", headers=MASTER)
        assert heal.status_code == 200, heal.text
        actions = [
            a["action"] for a in heal.json()["actions"] if a.get("outcome") == "applied"
        ]
        assert "create_missing_tables" in actions, heal.json()

        async with engine.connect() as conn:
            present = await conn.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='intelx_notices'"
                )
            )
            assert present.first() is not None, "explicit heal must recreate the table"
    finally:
        await engine.dispose()


# ── credentials come from Settings, not from a stray process environment (B21) ─


@pytest.mark.asyncio
async def test_friday_guard_follows_settings_not_the_process_environment(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """A conflicting FUTURIS_FRIDAY_API_KEY in the environment must not win.

    Before B21 the FRIDAY guard read ``os.getenv`` first, so a variable exported
    by the shell (or left over from another process) silently replaced the
    configured key, and the suite passed or failed depending on the caller's
    environment. Settings is now the single source of truth.
    """
    monkeypatch.setenv("FUTURIS_FRIDAY_API_KEY", "environment_value_not_the_setting_0123456789")
    resp = await client.get("/v1/friday/forecasts", headers=FRIDAY)
    assert resp.status_code == 200, resp.text


# ── disabled key enforcement is a development-only convenience (B20) ─────────


@pytest.mark.asyncio
async def test_disabled_auth_is_never_honoured_in_production(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """With key enforcement off, a production request must NOT become an admin.

    Before B20, API_KEYS_ENABLED=false granted every request the dev_admin role
    with scope '*' regardless of APP_ENV; the production guard did not look at
    the flag at all.
    """
    from futuris.infra.config import settings

    monkeypatch.setattr(settings, "API_KEYS_ENABLED", False)
    monkeypatch.setattr(settings, "APP_ENV", "production")
    resp = await client.get("/v1/audit")  # admin-only
    assert resp.status_code in (401, 403), resp.text


@pytest.mark.asyncio
async def test_disabled_auth_still_works_for_local_development(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """The development bypass is intentionally kept for local sandboxes."""
    from futuris.infra.config import settings

    monkeypatch.setattr(settings, "API_KEYS_ENABLED", False)
    monkeypatch.setattr(settings, "APP_ENV", "dev")
    resp = await client.get("/v1/audit")
    assert resp.status_code == 200, resp.text


# ── request sessions follow the current storage factory (B23) ───────────────


@pytest.mark.asyncio
async def test_request_sessions_follow_the_current_storage_factory(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """A request session must use the storage factory current at request time.

    ``deps.get_db_session`` used to import ``async_session_factory`` by name, so
    rebinding the storage module (as the isolation fixtures do) had no effect on
    request sessions: they kept targeting the process's default database. Found
    when universe tests received 503 ``storage_busy`` from a database another
    process held open.
    """
    from sqlalchemy import pool

    from futuris.api.deps import get_db_session
    from futuris.storage import db as storage_db

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'bound.db'}", poolclass=pool.NullPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(storage_db, "async_session_factory", factory)
    dependency = get_db_session()
    try:
        session = await dependency.__anext__()
        assert session.bind is engine, "request session bound to a stale storage factory"
    finally:
        await dependency.aclose()
        await engine.dispose()
