"""A storage failure inside a request must not become a dropped connection.

Two production symptoms, both observed while driving the live peer mesh:

1. ``INSERT INTO forecast_events`` hit ``FOREIGN KEY constraint failed``; the
   IntegrityError handler rendered a 409, then the request-scoped session -- left
   in "pending rollback" state -- raised ``PendingRollbackError`` from
   ``futuris/api/deps.py`` during dependency teardown.  The client saw
   ``httpx.ReadError`` instead of the 409 the API meant to send.
2. The market route swallowed the persistence error, returned 200 with
   ``forecast_id: null``, and published nothing: a success response for work
   that was never written.

These tests inject the *real* storage failure (a genuine foreign-key violation
against a file-backed SQLite database with ``foreign_keys=ON``) and assert on
what the client and the session see afterwards.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import futuris.api.deps as deps
import futuris.storage.db as storage_db
from futuris.api.app import app
from futuris.api.errors import FuturisAPIError
from futuris.api.routers import market as market_router
from futuris.connectors.base import Observation
from futuris.core.enums import ConfidenceLevel, ForecastStatus
from futuris.core.schemas import Forecast
from futuris.infra.config import settings
from futuris.storage.db import install_sqlite_pragmas
from futuris.storage.models import Base, ForecastEventModel
from futuris.storage.repositories import ForecastRepository

API_KEY = "session_teardown_key_0123456789abcdef"


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> AsyncGenerator[AsyncEngine, None]:
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'teardown.db'}")
    install_sqlite_pragmas(eng)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def client(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Client wired to the *real* ``get_db_session`` + a temp file database."""
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "FUTURIS_API_KEY", API_KEY)
    monkeypatch.setattr(storage_db, "async_session_factory", factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers={"X-API-Key": API_KEY}
    ) as http_client:
        yield http_client


def _broken_insert(session: AsyncSession) -> None:
    """Queue a real foreign-key violation (event for a forecast that never existed)."""
    session.add(
        ForecastEventModel(
            event_id=uuid4(),
            forecast_id=uuid4(),
            event_type="forecast_created",
            payload={"forecast_id": str(uuid4())},
            emitted_at=datetime.now(UTC),
        )
    )


def _forecast() -> Forecast:
    now = datetime.now(UTC)
    return Forecast(
        forecast_id=uuid4(),
        target="test:teardown",
        as_of=now,
        horizon=timedelta(hours=1),
        expires_at=now + timedelta(hours=1),
        review_at=now,
        prediction=1.0,
        range_lower=0.0,
        range_upper=2.0,
        probability=0.5,
        confidence=ConfidenceLevel.LOW,
        drivers=[],
        evidence=[],
        assumptions=[],
        model_version="test:teardown@v1",
        status=ForecastStatus.ACTIVE,
    )


@pytest.mark.asyncio
async def test_integrity_error_is_a_json_envelope_and_keeps_the_connection(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The historical mesh failure: a real FK violation during a forecast create."""

    async def failing_create(
        self: ForecastRepository, forecast: Forecast, idempotency_key: str | None = None
    ) -> Forecast:
        _broken_insert(self.session)
        await self.session.flush()  # raises IntegrityError, exactly like the mesh run
        return forecast  # pragma: no cover - unreachable

    monkeypatch.setattr(ForecastRepository, "create", failing_create)

    response = await client.post(
        "/v1/forecasts",
        json={"target": "test:teardown", "horizon": "24h"},
    )
    assert response.status_code == 409, response.text
    payload = response.json()
    assert payload["error"]["code"] == "conflict"
    # The raw constraint failure stays in the envelope details: it is what makes
    # a 409 actionable for the operator who has to find the offending row.
    assert "FOREIGN KEY constraint failed" in payload["error"]["details"]["dialect_error"]


@pytest.mark.asyncio
async def test_market_route_refuses_to_report_an_unpersisted_forecast(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A persistence failure must not produce a 200 advisory with a null id."""
    now = datetime.now(UTC)

    async def fake_fetch(self: object, start: datetime, end: datetime) -> list[Observation]:
        return [
            Observation(
                observed_at=now,
                source="stratex",
                series_id="market:volatility_index",
                value=0.42,
                unit="ratio",
            ),
            Observation(
                observed_at=now,
                source="stratex",
                series_id="portfolio:max_drawdown_pct",
                value=7.5,
                unit="percent",
            ),
            Observation(
                observed_at=now,
                source="stratex",
                series_id="portfolio:equity",
                value=125000.0,
                unit="usd",
            ),
        ]

    monkeypatch.setattr(market_router.TradingBotConnector, "fetch", fake_fetch)

    async def failing_create(
        self: ForecastRepository, forecast: Forecast, idempotency_key: str | None = None
    ) -> Forecast:
        _broken_insert(self.session)
        await self.session.flush()
        return forecast  # pragma: no cover - unreachable

    monkeypatch.setattr(ForecastRepository, "create", failing_create)

    response = await client.post(
        "/v1/market/forecast",
        json={"symbol": "BTC-USD", "include_intelx": False, "include_inference": False},
    )
    assert response.status_code == 503, response.text
    payload = response.json()
    assert payload["error"]["code"] == "storage_write_failed"
    assert "not be persisted" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_market_route_persists_and_labels_live_evidence(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The happy path of the same pipeline: persisted, live evidence, real id."""
    now = datetime.now(UTC)

    async def fake_fetch(self: object, start: datetime, end: datetime) -> list[Observation]:
        return [
            Observation(
                observed_at=now,
                source="stratex",
                series_id="market:volatility_index",
                value=0.42,
                unit="ratio",
            ),
            Observation(
                observed_at=now,
                source="stratex",
                series_id="portfolio:max_drawdown_pct",
                value=7.5,
                unit="percent",
            ),
        ]

    monkeypatch.setattr(market_router.TradingBotConnector, "fetch", fake_fetch)

    response = await client.post(
        "/v1/market/forecast",
        json={"symbol": "BTC-USD", "include_intelx": False, "include_inference": False},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["evidence_class"] == "live"
    assert payload["evidence_source"] == "stratex_telemetry"

    forecast_id = UUID(payload["forecast_id"])
    factory: async_sessionmaker[AsyncSession] = storage_db.async_session_factory  # type: ignore[assignment]
    async with factory() as session:
        stored = await ForecastRepository(session).get(forecast_id)
        assert stored is not None
        assert stored.evidence_class.value == "live"


@pytest.mark.asyncio
async def test_session_guard_reports_a_swallowed_storage_failure(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A handler that swallows a failed flush must not yield a false success.

    The generator is driven directly: first ``asend`` yields the session (the
    dependency is inside the request), then the flush fails and is swallowed
    (exactly what the market route used to do), then teardown must roll the
    session back and raise a mapped storage error -- not ``PendingRollbackError``.
    """
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(storage_db, "async_session_factory", factory)

    generator = deps.get_db_session()
    session = await generator.__anext__()
    _broken_insert(session)
    with pytest.raises(Exception) as flush_error:
        await session.flush()
    assert "FOREIGN KEY constraint failed" in str(flush_error.value)
    assert session.is_active is False
    try:
        with pytest.raises(FuturisAPIError) as teardown_error:
            await generator.__anext__()
        assert teardown_error.value.code == "storage_write_aborted"
        assert teardown_error.value.status_code == 503
    finally:
        await generator.aclose()

    # Self-healing: the next request gets a clean session and the write is gone.
    async with factory() as fresh:
        await fresh.execute(text("SELECT 1"))
        rows = (await fresh.execute(select(ForecastEventModel.event_id))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_teardown_never_masks_the_original_error(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The common path: the handler raised, so teardown re-raises the same error."""
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(storage_db, "async_session_factory", factory)

    generator = deps.get_db_session()
    session = await generator.__anext__()
    _broken_insert(session)
    with pytest.raises(Exception) as flush_error:
        await session.flush()

    try:
        with pytest.raises(type(flush_error.value)) as raised:
            await generator.athrow(flush_error.value)
        assert str(raised.value) == str(flush_error.value)
    finally:
        await generator.aclose()
    # The rollback ran, so the session is usable again even though the request
    # failed: the connection is never left in "pending rollback" state.
    assert session.is_active is True
    await session.close()
