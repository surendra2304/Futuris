"""Tests for storage layer CRUD operations on forecasts, evidence refs, and observations."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from futuris.connectors.base import Observation
from futuris.core.enums import (
    ConfidenceLevel,
    EvidenceClass,
    ForecastStatus,
    SignalClass,
    SourceTrust,
)
from futuris.core.schemas import Driver, EvidenceRef, Forecast
from futuris.storage.models import Base
from futuris.storage.repositories import (
    EvidenceRepository,
    ForecastRepository,
    ObservationRepository,
)

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Create a pristine isolated test database schema and session."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_forecast_crud_cycle(db_session: AsyncSession) -> None:
    forecast_repo = ForecastRepository(db_session)
    forecast_id = uuid4()
    now = datetime.now(UTC)

    f = Forecast(
        forecast_id=forecast_id,
        target="service:checkout:capacity_exceedance_24h",
        as_of=now,
        horizon=timedelta(hours=24),
        expires_at=now + timedelta(hours=24),
        prediction=100.0,
        range_lower=90.0,
        range_upper=110.0,
        probability=0.75,
        confidence=ConfidenceLevel.HIGH,
        model_version="statsforecast.AutoARIMA:v1.0",
        review_at=now + timedelta(hours=12),
        status=ForecastStatus.ACTIVE,
        evidence_class=EvidenceClass.LIVE,
        evidence_source="telemetry:live_mesh",
    )

    created = await forecast_repo.create(f)
    assert created.forecast_id == forecast_id
    assert created.target == "service:checkout:capacity_exceedance_24h"
    assert created.evidence_class == EvidenceClass.LIVE

    fetched = await forecast_repo.get(forecast_id)
    assert fetched is not None
    assert fetched.forecast_id == forecast_id
    assert fetched.prediction == 100.0

    listed = await forecast_repo.list_filtered(target="service:checkout:capacity_exceedance_24h")
    assert len(listed) >= 1
    assert listed[0].forecast_id == forecast_id

    # Test delete
    deleted = await forecast_repo.delete(forecast_id)
    assert deleted is True
    assert await forecast_repo.get(forecast_id) is None


@pytest.mark.asyncio
async def test_evidence_ref_crud_cycle(db_session: AsyncSession) -> None:
    forecast_repo = ForecastRepository(db_session)
    evidence_repo = EvidenceRepository(db_session)
    forecast_id = uuid4()
    now = datetime.now(UTC)

    ev_id = uuid4()
    ev = EvidenceRef(
        evidence_id=ev_id,
        source="telemetry:live:ingress",
        source_trust=SourceTrust.HIGH,
        signal_class=SignalClass.TELEMETRY,
        as_of=now,
        snapshot_path="/var/futuris/evidence/snap.parquet",
        content_hash="a" * 64,
        evidence_class=EvidenceClass.LIVE,
    )

    f = Forecast(
        forecast_id=forecast_id,
        target="service:payment:latency_exceedance_1h",
        as_of=now,
        horizon=timedelta(hours=1),
        expires_at=now + timedelta(hours=1),
        prediction=45.0,
        range_lower=40.0,
        range_upper=50.0,
        confidence=ConfidenceLevel.MEDIUM,
        evidence=[ev],
        drivers=[
            Driver(
                name="cpu_pressure",
                direction="positive",
                strength=0.8,
                leading_or_lagging="leading",
                evidence_refs=[ev_id],
            )
        ],
        model_version="ensemble:v2.0",
        review_at=now + timedelta(minutes=30),
        status=ForecastStatus.ACTIVE,
        evidence_class=EvidenceClass.LIVE,
    )

    await forecast_repo.create(f)

    # Fetch evidence by ID
    stored_ev = await evidence_repo.get(ev_id)
    assert stored_ev is not None
    assert stored_ev.evidence_id == ev_id
    assert stored_ev.source == "telemetry:live:ingress"
    assert stored_ev.evidence_class == EvidenceClass.LIVE

    # List evidence by forecast
    ev_list = await evidence_repo.list_by_forecast(forecast_id)
    assert len(ev_list) == 1
    assert ev_list[0].evidence_id == ev_id

    # Delete evidence ref
    deleted = await evidence_repo.delete(ev_id)
    assert deleted is True
    assert await evidence_repo.get(ev_id) is None


@pytest.mark.asyncio
async def test_observation_repository_crud(db_session: AsyncSession) -> None:
    obs_repo = ObservationRepository(db_session)
    now = datetime.now(UTC)

    obs1 = Observation(
        observed_at=now - timedelta(minutes=10),
        source="dokploy_telemetry",
        series_id="service:checkout:requests_per_second",
        value=1500.0,
        unit="req/s",
        tags={"region": "oracle-always-free", "env": "prod"},
    )
    obs2 = Observation(
        observed_at=now,
        source="dokploy_telemetry",
        series_id="service:checkout:requests_per_second",
        value=1650.0,
        unit="req/s",
        tags={"region": "oracle-always-free", "env": "prod"},
    )

    # Batch insert
    batch = await obs_repo.record_batch([obs1, obs2])
    assert len(batch) == 2

    # Query series
    series_obs = await obs_repo.list_by_series("service:checkout:requests_per_second")
    assert len(series_obs) == 2
    # Verify ordered newest first
    assert series_obs[0].value == 1650.0
    assert series_obs[1].value == 1500.0

    # Get latest
    latest = await obs_repo.get_latest("service:checkout:requests_per_second")
    assert latest is not None
    assert latest.value == 1650.0

    # Single insert
    obs3 = Observation(
        observed_at=now + timedelta(minutes=5),
        source="dokploy_telemetry",
        series_id="service:checkout:requests_per_second",
        value=1700.0,
        unit="req/s",
        tags={"region": "oracle-always-free"},
    )
    await obs_repo.record_observation(obs3)

    latest_updated = await obs_repo.get_latest("service:checkout:requests_per_second")
    assert latest_updated is not None
    assert latest_updated.value == 1700.0

    # Delete by series
    deleted_count = await obs_repo.delete_by_series("service:checkout:requests_per_second")
    assert deleted_count == 3
    assert await obs_repo.get_latest("service:checkout:requests_per_second") is None
