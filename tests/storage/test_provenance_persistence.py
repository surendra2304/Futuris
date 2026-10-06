"""Provenance labels must survive the database, and a stale DB must heal itself.

Two failures pinned here:

* ``Forecast.evidence_class``/``evidence_source`` and ``EvidenceRef.evidence_class``
  were domain-only fields.  A forecast built from live Stratex telemetry was
  written without them and read back as ``synthetic`` -- every GET contradicted
  the evidence the row was created from (caught by
  ``tests/api/test_session_teardown.py::test_market_route_persists_and_labels_live_evidence``).
* ``create_all`` cannot add a column to an existing table, so introducing those
  columns would have turned every read against an existing SQLite file into
  ``no such column`` until an operator intervened.  ``add_missing_columns``
  closes that gap on startup, the same way Alembic migrations do for Postgres.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from futuris.core.enums import (
    ConfidenceLevel,
    EvidenceClass,
    ForecastStatus,
    SignalClass,
    SourceTrust,
)
from futuris.core.hashing import content_hash_of
from futuris.core.schemas import EvidenceRef, Forecast
from futuris.storage.db import add_missing_columns, install_sqlite_pragmas
from futuris.storage.models import Base, ForecastModel
from futuris.storage.repositories import ForecastRepository


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> AsyncGenerator[AsyncEngine, None]:
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'provenance.db'}")
    install_sqlite_pragmas(eng)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


def _live_forecast() -> Forecast:
    now = datetime.now(UTC)
    evidence = EvidenceRef(
        evidence_id=uuid4(),
        source="stratex:telemetry",
        source_trust=SourceTrust.HIGH,
        signal_class=SignalClass.TELEMETRY,
        as_of=now,
        snapshot_path="inline://market/BTC-USD/stratex-telemetry.json",
        content_hash=content_hash_of({"volatility": 0.42}),
        evidence_class=EvidenceClass.LIVE,
    )
    return Forecast(
        forecast_id=uuid4(),
        target="market:crypto:BTC-USD:volatility_24h",
        as_of=now,
        horizon=timedelta(hours=24),
        expires_at=now + timedelta(hours=24),
        review_at=now + timedelta(hours=6),
        prediction=0.42,
        range_lower=-2.5,
        range_upper=3.5,
        probability=0.55,
        confidence=ConfidenceLevel.MEDIUM,
        drivers=[],
        evidence=[evidence],
        assumptions=["stratex telemetry"],
        model_version="statsforecast:market_volatility@v2",
        status=ForecastStatus.ACTIVE,
        evidence_class=EvidenceClass.LIVE,
        evidence_source="stratex_telemetry",
    )


@pytest.mark.asyncio
async def test_live_provenance_survives_the_round_trip(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    forecast = _live_forecast()
    async with session_factory() as session:
        await ForecastRepository(session).create(forecast)
        await session.commit()

    async with session_factory() as session:
        stored = await ForecastRepository(session).get(forecast.forecast_id)
        assert stored is not None
        assert stored.evidence_class == EvidenceClass.LIVE
        assert stored.evidence_source == "stratex_telemetry"
        assert stored.evidence[0].evidence_class == EvidenceClass.LIVE


@pytest.mark.asyncio
async def test_a_forecast_without_labels_reads_back_as_synthetic(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Legacy rows (written before the columns existed) must degrade honestly."""
    forecast = _live_forecast()
    async with session_factory() as session:
        await ForecastRepository(session).create(forecast)
        await session.commit()

    async with session_factory() as session:
        # Simulate a row written by the previous release.
        await session.execute(
            text("UPDATE forecasts SET evidence_class = NULL, evidence_source = NULL")
        )
        await session.commit()

    async with session_factory() as session:
        stored = await ForecastRepository(session).get(forecast.forecast_id)
        assert stored is not None
        assert stored.evidence_class == EvidenceClass.SYNTHETIC
        assert stored.evidence_source is None


@pytest.mark.asyncio
async def test_startup_adds_columns_a_stale_database_is_missing(
    engine: AsyncEngine,
) -> None:
    """A DB created before a column existed must be repaired, not rejected."""
    async with engine.begin() as conn:
        # SQLite refuses to drop a column that an index still references.
        await conn.exec_driver_sql("DROP INDEX ix_forecasts_evidence_class")
        await conn.exec_driver_sql("ALTER TABLE forecasts DROP COLUMN evidence_class")
        await conn.exec_driver_sql("ALTER TABLE forecasts DROP COLUMN evidence_source")
        await conn.exec_driver_sql("ALTER TABLE evidence_refs DROP COLUMN evidence_class")

    added = await add_missing_columns(engine)
    assert set(added) == {
        "forecasts.evidence_class",
        "forecasts.evidence_source",
        "evidence_refs.evidence_class",
    }
    async with engine.begin() as conn:
        listed = (await conn.exec_driver_sql("PRAGMA index_list('forecasts')")).fetchall()
        indexes = {row[1] for row in listed}
    assert "ix_forecasts_evidence_class" in indexes, "the declared index must be recreated too"

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    forecast = _live_forecast()
    async with factory() as session:
        await ForecastRepository(session).create(forecast)
        await session.commit()
    async with factory() as session:
        stored = await ForecastRepository(session).get(forecast.forecast_id)
        assert stored is not None
        assert stored.evidence_class == EvidenceClass.LIVE


@pytest.mark.asyncio
async def test_repair_is_idempotent(engine: AsyncEngine) -> None:
    assert await add_missing_columns(engine) == []
    assert await add_missing_columns(engine) == []


@pytest.mark.asyncio
async def test_columns_not_addable_safely_are_reported_not_guessed(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A NOT NULL column without a constant default cannot be added by SQLite."""
    async with engine.begin() as conn:
        await conn.exec_driver_sql("ALTER TABLE forecasts DROP COLUMN evidence_source")

    from futuris.storage import db as storage_db

    column = ForecastModel.__table__.columns["evidence_source"]
    monkeypatch.setattr(column, "nullable", False, raising=False)
    assert storage_db._sqlite_add_column_ddl("forecasts", column, engine.dialect) is None


@pytest.mark.asyncio
async def test_new_rows_are_queryable_by_evidence_class(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await ForecastRepository(session).create(_live_forecast())
        await session.commit()

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(ForecastModel.forecast_id).where(
                    ForecastModel.evidence_class == EvidenceClass.LIVE.value
                )
            )
        ).scalars().all()
        assert len(rows) == 1
