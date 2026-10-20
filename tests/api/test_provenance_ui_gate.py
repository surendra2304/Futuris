"""Tests for Safety Invariant 2 (Input Provenance Gate) and verifiable telemetry badges."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from futuris.api.app import app
from futuris.api.deps import get_db_session
from futuris.core.enums import ConfidenceLevel, EvidenceClass, ForecastStatus
from futuris.core.schemas import Forecast
from futuris.storage.models import Base
from futuris.storage.repositories import ForecastRepository

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")


@pytest_asyncio.fixture
async def test_db_session() -> AsyncSession:
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_provenance_gate_on_forecast_listings(test_db_session: AsyncSession) -> None:
    f_repo = ForecastRepository(test_db_session)
    now = datetime.now(UTC)

    # 1. Create a forecast with synthetic evidence (unverified)
    f_synthetic = Forecast(
        forecast_id=uuid4(),
        target="service:checkout:capacity_exceedance_24h",
        as_of=now,
        horizon=timedelta(hours=24),
        expires_at=now + timedelta(hours=24),
        prediction=100.0,
        range_lower=90.0,
        range_upper=110.0,
        confidence=ConfidenceLevel.MEDIUM,
        model_version="statsforecast.AutoARIMA:v1.0",
        review_at=now + timedelta(hours=12),
        status=ForecastStatus.ACTIVE,
        evidence_class=EvidenceClass.SYNTHETIC,
    )
    await f_repo.create(f_synthetic)

    # 2. Create a forecast with live measured evidence
    f_live = Forecast(
        forecast_id=uuid4(),
        target="service:payment:throughput_24h",
        as_of=now,
        horizon=timedelta(hours=24),
        expires_at=now + timedelta(hours=24),
        prediction=250.0,
        range_lower=240.0,
        range_upper=260.0,
        confidence=ConfidenceLevel.HIGH,
        model_version="statsforecast.AutoARIMA:v1.0",
        review_at=now + timedelta(hours=12),
        status=ForecastStatus.ACTIVE,
        evidence_class=EvidenceClass.LIVE,
    )
    await f_repo.create(f_live)

    async def override_get_db_session():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_get_db_session

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/forecasts")
            assert resp.status_code == 200
            data = resp.json()

            # Find both forecasts
            syn_item = next(i for i in data if i["forecast_id"] == str(f_synthetic.forecast_id))
            live_item = next(i for i in data if i["forecast_id"] == str(f_live.forecast_id))

            # Synthetic forecast: provenance gate unverified
            assert syn_item["evidence_class"] == "synthetic"
            assert syn_item["input_telemetry_verified"] is False
            assert syn_item["prediction_is_not_authorization"] is True
            assert syn_item["executable_commands"] == []

            # Live forecast: provenance gate verified
            assert live_item["evidence_class"] == "live"
            assert live_item["input_telemetry_verified"] is True
            assert live_item["prediction_is_not_authorization"] is True
            assert live_item["executable_commands"] == []
    finally:
        app.dependency_overrides.clear()
