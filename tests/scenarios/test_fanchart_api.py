"""Tests for Monte Carlo Quantile Fan-Chart simulation endpoint and streaming."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from futuris.api.app import app
from futuris.api.routers.streaming import stream_hub
from futuris.core.enums import ConfidenceLevel, ForecastStatus
from futuris.core.schemas import Forecast


@pytest.mark.asyncio
async def test_standalone_fan_chart_simulation():
    """Verify POST /v1/scenarios/monte-carlo/fan-chart generates ordered percentiles."""
    sub_id, queue = await stream_hub.register(target_filter="BTCUSDT")

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            req = {
                "target": "BTCUSDT",
                "base_value": 50000.0,
                "horizon_steps": 10,
                "num_samples": 500,
                "drift": 0.002,
                "volatility": 0.02,
            }
            resp = await client.post("/v1/scenarios/monte-carlo/fan-chart", json=req)
            assert resp.status_code == 200
            data = resp.json()

            assert data["target"] == "BTCUSDT"
            assert data["base_value"] == 50000.0
            assert data["horizon_steps"] == 10
            assert data["num_samples"] == 500
            assert data["prediction_is_not_authorization"] is True

            fan_chart = data["fan_chart"]
            assert len(fan_chart) == 10

            for s in fan_chart:
                assert s["step"] >= 1
                # Check quantile monotonicity: p05 <= p10 <= p25 <= p50 <= p75 <= p90 <= p95 <= p99
                assert s["p05"] <= s["p10"] + 1e-4
                assert s["p10"] <= s["p25"] + 1e-4
                assert s["p25"] <= s["p50"] + 1e-4
                assert s["p50"] <= s["p75"] + 1e-4
                assert s["p75"] <= s["p90"] + 1e-4
                assert s["p90"] <= s["p95"] + 1e-4
                assert s["p95"] <= s["p99"] + 1e-4

            assert len(data["trajectories_sample"]) == 5
            assert len(data["trajectories_sample"][0]) == 10

        # Verify broadcast over StreamHub
        msg = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert msg["type"] == "fan_chart_simulation"
        assert msg["target"] == "BTCUSDT"
        assert len(msg["data"]["fan_chart"]) == 10
    finally:
        await stream_hub.unregister(sub_id)


@pytest.mark.asyncio
async def test_forecast_anchored_fan_chart():
    """Verify POST /v1/forecasts/{forecast_id}/scenarios/fan-chart anchors to parent prediction."""
    from datetime import timedelta

    from futuris.api.deps import get_forecast_repo
    from futuris.infra.auth import AuthUser, require_analyst

    now = datetime.now(UTC)
    forecast_id = uuid4()
    f = Forecast(
        forecast_id=forecast_id,
        target="trading:ETHUSDT:price_variance_24h",
        prediction=3200.0,
        range_lower=2800.0,
        range_upper=3600.0,
        probability=0.85,
        confidence=ConfidenceLevel.HIGH,
        model_version="test:v1.0",
        as_of=now,
        horizon=timedelta(hours=24),
        expires_at=now + timedelta(hours=24),
        review_at=now + timedelta(hours=6),
        status=ForecastStatus.ACTIVE,
    )

    class FakeForecastRepo:
        async def get(self, fid):
            if fid == forecast_id:
                return f
            return None

    app.dependency_overrides[get_forecast_repo] = lambda: FakeForecastRepo()
    app.dependency_overrides[require_analyst] = lambda: AuthUser(
        label="test_analyst", role="analyst", principal_id="a1", tenant_id="t1", scopes=["*"]
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/v1/forecasts/{forecast_id}/scenarios/fan-chart?horizon_steps=6&num_samples=400",
                headers={"X-API-Key": "test_analyst_key"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["target"] == "trading:ETHUSDT:price_variance_24h"
            assert data["base_value"] == 3200.0
            assert data["horizon_steps"] == 6
            assert len(data["fan_chart"]) == 6
    finally:
        app.dependency_overrides.pop(get_forecast_repo, None)
        app.dependency_overrides.pop(require_analyst, None)
