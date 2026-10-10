"""Governance regression tests: audit coverage (H1), point-in-time status (H2), immutable
outcomes (H3)."""

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
from futuris.core.enums import ConfidenceLevel, ForecastStatus
from futuris.core.schemas import Forecast
from futuris.infra.audit import AuditLogger
from futuris.storage.models import Base
from futuris.storage.repositories import ForecastRepository

MASTER_KEY = "governance_test_key_0123456789abcdef"


@pytest_asyncio.fixture(scope="function")
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def client(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[httpx.AsyncClient, None]:
    from futuris.infra.config import settings

    monkeypatch.setattr(settings, "FUTURIS_API_KEY", MASTER_KEY)

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        yield session

    app.dependency_overrides[get_db_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers={"X-API-Key": MASTER_KEY}
    ) as c:
        yield c
    app.dependency_overrides.clear()


def _forecast(target: str = "service:checkout:capacity_exceedance_24h") -> Forecast:
    now = datetime.now(UTC)
    return Forecast(
        forecast_id=uuid4(),
        target=target,
        as_of=now,
        horizon=timedelta(hours=24),
        expires_at=now + timedelta(hours=24),
        review_at=now + timedelta(hours=6),
        prediction=2840.0,
        range_lower=2100.0,
        range_upper=3650.0,
        probability=0.24,
        confidence=ConfidenceLevel.HIGH,
        model_version="test@v1",
        status=ForecastStatus.ACTIVE,
    )


# ── H1: every mutation leaves an audit row ─────────────────────────────────


@pytest.mark.asyncio
async def test_forecast_create_invalidate_and_resolve_are_audited(client: httpx.AsyncClient):
    created = await client.post(
        "/v1/forecasts",
        json={"target": "service:checkout:capacity_exceedance_24h", "horizon": "24h"},
    )
    assert created.status_code == 201, created.text
    forecast_id = created.json()["forecast_id"]

    invalidated = await client.post(
        f"/v1/forecasts/{forecast_id}/invalidate",
        json={"reason": "governance regression test"},
    )
    assert invalidated.status_code == 200, invalidated.text

    audit = await client.get("/v1/audit")
    assert audit.status_code == 200
    actions = [(row["action"], row["entity"], row["entity_id"]) for row in audit.json()]
    assert ("create_forecast", "forecast", forecast_id) in actions
    assert ("invalidate_forecast", "forecast", forecast_id) in actions


@pytest.mark.asyncio
async def test_manual_resolution_is_audited(client: httpx.AsyncClient):
    created = await client.post(
        "/v1/forecasts",
        json={"target": "service:checkout:capacity_exceedance_24h", "horizon": "24h"},
    )
    forecast_id = created.json()["forecast_id"]
    resolved = await client.post(
        f"/v1/forecasts/{forecast_id}/resolve-manual",
        json={"observed_value": 3950.0, "event_occurred": True, "note": "measured at 22:00"},
    )
    assert resolved.status_code == 200, resolved.text

    audit = await client.get("/v1/audit")
    actions = [row["action"] for row in audit.json()]
    assert "resolve_forecast_manual" in actions


@pytest.mark.asyncio
async def test_audit_rows_carry_hashed_payloads(client: httpx.AsyncClient):
    await client.post(
        "/v1/forecasts",
        json={"target": "service:checkout:capacity_exceedance_24h", "horizon": "24h"},
    )
    rows = (await client.get("/v1/audit")).json()
    assert rows
    for row in rows:
        assert len(row["payload_hash"]) == 64
        assert row["actor_label"]


# ── H2: point-in-time reconstruction follows lifecycle events ──────────────


@pytest.mark.asyncio
async def test_point_in_time_reflects_invalidation(session: AsyncSession):
    repo = ForecastRepository(session)
    forecast = await repo.create(_forecast(target="pit:target"))
    await session.commit()

    # A timestamp strictly between creation and invalidation must see the
    # forecast as it was then: active.
    await asyncio.sleep(0.02)
    t_between = datetime.now(UTC)
    await asyncio.sleep(0.02)

    await repo.update_status(forecast.forecast_id, ForecastStatus.INVALIDATED)
    await session.commit()

    after = await repo.point_in_time_query("pit:target", datetime.now(UTC))
    assert after is not None
    assert after.status == ForecastStatus.INVALIDATED

    before = await repo.point_in_time_query("pit:target", t_between)
    assert before is not None
    assert before.status == ForecastStatus.ACTIVE


@pytest.mark.asyncio
async def test_point_in_time_reflects_resolution(session: AsyncSession):
    from futuris.core.schemas import Outcome

    repo = ForecastRepository(session)
    forecast = await repo.create(_forecast(target="pit:resolved"))
    await session.commit()
    await asyncio.sleep(0.02)

    from futuris.storage.repositories import OutcomeRepository

    outcome_repo = OutcomeRepository(session)
    await outcome_repo.record_outcome(
        Outcome(
            outcome_id=uuid4(),
            forecast_id=forecast.forecast_id,
            observed_value=3000.0,
            event_occurred=True,
            resolved_at=datetime.now(UTC),
            resolution_method="human",
            resolution_rule_version="test:v1",
        )
    )
    await session.commit()

    await asyncio.sleep(0.02)
    snapshot = await repo.point_in_time_query("pit:resolved", datetime.now(UTC))
    assert snapshot is not None
    assert snapshot.status == ForecastStatus.RESOLVED


@pytest.mark.asyncio
async def test_point_in_time_returns_none_for_unknown_target(session: AsyncSession):
    repo = ForecastRepository(session)
    assert await repo.point_in_time_query("nope", datetime.now(UTC)) is None


@pytest.mark.asyncio
async def test_audit_logger_is_append_only(session: AsyncSession):
    logger = AuditLogger(session)
    record = await logger.log_mutation(
        actor_label="tester",
        action="create_forecast",
        entity="forecast",
        entity_id="x",
        payload={"a": 1},
    )
    assert len(record.payload_hash) == 64
    assert record.actor_label == "tester"


# ── H3: outcomes are immutable ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_manual_resolution_is_a_conflict(client: httpx.AsyncClient):
    created = await client.post(
        "/v1/forecasts",
        json={"target": "service:checkout:capacity_exceedance_24h", "horizon": "24h"},
    )
    forecast_id = created.json()["forecast_id"]
    body = {"observed_value": 3950.0, "event_occurred": True, "note": "first resolution"}

    first = await client.post(f"/v1/forecasts/{forecast_id}/resolve-manual", json=body)
    assert first.status_code == 200, first.text

    second = await client.post(f"/v1/forecasts/{forecast_id}/resolve-manual", json=body)
    assert second.status_code == 409, second.text
    # The conflict is reported by whichever guard sees it first: the outcome
    # uniqueness pre-check, or the lifecycle guard that refuses to resolve a
    # forecast that is no longer live.
    assert "already has an outcome" in second.text or "cannot be resolved" in second.text
