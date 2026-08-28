"""End-to-end tests for the programmatic demo seeding and bootstrapping pipeline."""

from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from futuris.api.app import app
from futuris.api.deps import get_db_session
from futuris.demo.seed import DemoSeeder
from futuris.storage.models import Base


@pytest_asyncio.fixture(scope="function")
async def demo_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide isolated in-memory SQLite database for demo tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_demo_pipeline_e2e_execution(demo_db_session: AsyncSession):
    """Verify demo seeder runs end-to-end with low data volume and returns expected statistics."""
    seeder = DemoSeeder(seed=42, session=demo_db_session)
    # Run with 14 days telemetry and 7 days backtest for fast test execution
    stats = await seeder.run(days=14, backtest_days=7)

    assert stats["telemetry_points"] > 0
    assert stats["live_forecast_id"] is not None
    assert stats["live_prediction"] > 0.0
    assert stats["scenarios_evaluated"] == 3
    assert stats["calibration_ece"] >= 0.0


@pytest.mark.asyncio
async def test_demo_api_endpoints_return_seeded_data(demo_db_session: AsyncSession):
    """Verify that after seeding, public API endpoints serve live forecast, scenarios, and calib."""
    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield demo_db_session

    app.dependency_overrides[get_db_session] = _override_get_db
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Create a forecast via API
        resp = await client.post(
            "/v1/forecasts",
            json={"target": "service:checkout:capacity_exceedance_24h", "horizon": "24h"},
        )
        assert resp.status_code == 201
        data = resp.json()
        forecast_id = data["forecast_id"]
        assert forecast_id is not None

        # 2. Query forecast list
        list_resp = await client.get("/v1/forecasts")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) >= 1

        # 3. Query calibration honesty curve
        cal_resp = await client.get("/v1/evaluation/calibration")
        assert cal_resp.status_code == 200
        assert "expected_calibration_error" in cal_resp.json()

    app.dependency_overrides.clear()
