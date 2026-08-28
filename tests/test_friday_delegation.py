"""Tests for FRIDAY delegation endpoints, scenarios, calibration reports, and rate limiting."""


import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from futuris.api.app import app
from futuris.api.deps import get_db_session
from futuris.storage.models import Base


@pytest.fixture
async def friday_test_db():
    """Isolated in-memory SQLite database for FRIDAY delegation tests."""
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
async def test_friday_forecast_delegation_authorized(
    friday_test_db: AsyncSession, monkeypatch
):
    """Verify POST /v1/friday/forecast receives delegation and returns response."""
    monkeypatch.setenv("FUTURIS_FRIDAY_API_KEY", "friday_test_key_123")

    async def _override_get_db():
        yield friday_test_db

    app.dependency_overrides[get_db_session] = _override_get_db
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        # 1. Test unauthorized without key
        unauth_resp = await client.post(
            "/v1/friday/forecast",
            json={
                "friday_request_id": "req_101",
                "target": "service:checkout:capacity_exceedance_24h",
                "horizon": "24h",
            },
        )
        assert unauth_resp.status_code == 401

        # 2. Test authorized request
        resp = await client.post(
            "/v1/friday/forecast",
            headers={"X-API-Key": "friday_test_key_123"},
            json={
                "friday_request_id": "req_101",
                "target": "service:checkout:capacity_exceedance_24h",
                "horizon": "24h",
                "confidence_level": 0.90,
                "context": {"requesting_system": "FRIDAY_AUTO_SCALER"},
                "priority": "urgent",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["friday_request_id"] == "req_101"
        assert "prediction" in data
        assert data["prediction"]["point_estimate"] > 0
        assert "probability_distribution" in data["prediction"]
        assert len(data["drivers_identified"]) > 0

        # 3. Test scenario evaluation POST /v1/friday/scenario
        forecast_id = data["futuris_forecast_id"]
        scen_resp = await client.post(
            "/v1/friday/scenario",
            headers={"X-API-Key": "friday_test_key_123"},
            json={
                "question": "What if traffic doubles?",
                "base_forecast_id": forecast_id,
                "scenario_spec": {
                    "variable_changes": [{"metric": "demand", "change_pct": 100.0}],
                    "assumptions": ["Cluster size constant"],
                },
            },
        )
        assert scen_resp.status_code == 200
        scen_data = scen_resp.json()
        assert scen_data["divergent_prediction"] > data["prediction"]["point_estimate"]
        assert scen_data["risk_assessment"] == "HIGH_RISK"
        assert "comparison_to_baseline" in scen_data

        # 4. List forecasts GET /v1/friday/forecasts
        list_resp = await client.get(
            "/v1/friday/forecasts",
            headers={"X-API-Key": "friday_test_key_123"},
        )
        assert list_resp.status_code == 200
        assert len(list_resp.json()) >= 1

        # 5. Get FRIDAY calibration report GET /v1/friday/calibration
        cal_resp = await client.get(
            "/v1/friday/calibration",
            headers={"X-API-Key": "friday_test_key_123"},
        )
        assert cal_resp.status_code == 200
        cal_data = cal_resp.json()
        assert "overall_ece" in cal_data
        assert (
            "service:checkout:capacity_exceedance_24h"
            in cal_data["per_target_type_calibration"]
        )
        assert cal_data["trend"] in ["improving", "degrading", "stable"]

    app.dependency_overrides.clear()
