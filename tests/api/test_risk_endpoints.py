"""Tests for quantitative tail-risk audit, VaR/CVaR, and real-time streaming."""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from futuris.api.app import app
from futuris.api.routers.streaming import stream_hub
from futuris.connectors.base import Observation
from futuris.core.enums import SignalClass


@pytest.mark.asyncio
async def test_risk_audit_endpoint_fallback():
    """Verify GET /v1/risk/audit/{target} executes VaR/CVaR calibration audit."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/risk/audit/BTCUSDT?window_days=30&confidence_level=0.95")
        assert resp.status_code == 200
        data = resp.json()

        assert data["target"] == "BTCUSDT"
        assert data["prediction_is_not_authorization"] is True
        assert data["sample_size"] >= 10
        assert data["basel_traffic_light"] in {"green", "yellow", "red"}

        # Metrics 95 & 99
        m95 = data["metrics_95"]
        assert m95["confidence_level"] == 0.95
        assert "var_historical" in m95
        assert "var_parametric" in m95
        assert "cvar_expected_shortfall" in m95
        # Expected Shortfall must be >= VaR
        assert m95["cvar_expected_shortfall"] >= m95["var_historical"]

        m99 = data["metrics_99"]
        assert m99["confidence_level"] == 0.99
        assert m99["cvar_expected_shortfall"] >= m99["var_historical"]

        # Kupiec POF tests
        k95 = data["kupiec_test_95"]
        assert k95["confidence_level"] == 0.95
        assert "statistic" in k95
        assert "p_value" in k95
        assert isinstance(k95["is_rejected"], bool)

        # Christoffersen independence test
        c_ind = data["christoffersen_independence_test"]
        assert "is_rejected" in c_ind
        assert "statistic" in c_ind


@pytest.mark.asyncio
async def test_risk_audit_with_real_observations(monkeypatch):
    """Verify GET /v1/risk/audit uses existing observations when available in database."""
    obs_batch = [
        Observation(
            observed_at=datetime.now(UTC),
            source="test:live",
            series_id="trading:ETHUSDT:price",
            value=2000.0 * (1.0 + 0.01 * (i % 5 - 2)),
            unit="USD",
            tags={"signal_class": SignalClass.TELEMETRY.value},
        )
        for i in range(25)
    ]

    from futuris.api.deps import get_observation_repo

    class FakeObsRepo:
        def __init__(self, *args, **kwargs):
            pass

        async def list_by_series(self, series_id, limit=500, since=None, until=None):
            if "ETHUSDT" in series_id:
                return obs_batch
            return []

    app.dependency_overrides[get_observation_repo] = lambda: FakeObsRepo()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/risk/audit/ETHUSDT")
            assert resp.status_code == 200
            data = resp.json()
            assert data["target"] == "ETHUSDT"
            assert data["sample_size"] == 24  # 25 prices -> 24 returns
    finally:
        app.dependency_overrides.pop(get_observation_repo, None)


@pytest.mark.asyncio
async def test_risk_stream_update_broadcasts_to_subscribers():
    """Verify POST /v1/risk/stream-update broadcasts risk_calibration_update to StreamHub."""
    # Register test consumer to stream_hub
    sub_id, queue = await stream_hub.register(target_filter="SOLUSDT")

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "target": "SOLUSDT",
                "sample_size": 150,
            }
            resp = await client.post("/v1/risk/stream-update", json=payload)
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "broadcast_sent"
            assert body["subscribers_delivered"] >= 1

        # Check that event was delivered to queue
        event = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert event["type"] == "risk_calibration_update"
        assert event["target"] == "SOLUSDT"
        assert "metrics_95" in event["data"]
        assert "metrics_99" in event["data"]
        assert event["data"]["prediction_is_not_authorization"] is True
    finally:
        await stream_hub.unregister(sub_id)


@pytest.mark.asyncio
async def test_stratex_trade_pairs_endpoint():
    """Verify GET /v1/risk/stratex/pairs discovers active pairs from Stratex."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/risk/stratex/pairs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "stratex"
        assert isinstance(data["active_pairs"], list)
        assert len(data["active_pairs"]) >= 3
        assert "BTCUSDT" in data["active_pairs"]
        assert "ETHUSDT" in data["active_pairs"]
        assert "SOLUSDT" in data["active_pairs"]


@pytest.mark.asyncio
async def test_portfolio_tail_risk_endpoint():
    """Verify GET /v1/risk/portfolio computes portfolio-wide VaR/CVaR and positions."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/risk/portfolio?window_days=30&confidence_level=0.95")
        assert resp.status_code == 200
        data = resp.json()

        assert data["prediction_is_not_authorization"] is True
        assert isinstance(data["active_pairs"], list)
        assert len(data["active_pairs"]) >= 3

        # Positions
        assert isinstance(data["positions"], list)
        assert len(data["positions"]) >= 1
        pos = data["positions"][0]
        assert "symbol" in pos
        assert "size" in pos
        assert "entry_price" in pos
        assert "unrealized_pnl" in pos

        # Assets risk reports
        assert isinstance(data["assets"], list)
        assert len(data["assets"]) >= 3
        asset_item = data["assets"][0]
        assert "symbol" in asset_item
        assert "var_95" in asset_item
        assert "cvar_95" in asset_item
        assert "basel_traffic_light" in asset_item
        assert asset_item["cvar_95"] >= asset_item["var_95"]

        # Portfolio Summary
        summary = data["portfolio_summary"]
        assert "total_exposure_usd" in summary
        assert "portfolio_var_95" in summary
        assert "portfolio_cvar_95" in summary
        assert summary["overall_traffic_light"] in {"green", "yellow", "red"}
        assert "highest_risk_asset" in summary


@pytest.mark.asyncio
async def test_calculate_portfolio_risk_payload_and_cache(monkeypatch):
    """Verify calculate_portfolio_risk_payload populates get_latest_portfolio_risk."""
    from futuris.api.routers.risk import (
        calculate_portfolio_risk_payload,
        get_latest_portfolio_risk,
    )
    from futuris.connectors.trading_bot import TradingBotConnector

    # Mock connector to prevent network timeout
    monkeypatch.setattr(
        TradingBotConnector,
        "discover_active_trade_pairs",
        lambda self: asyncio.sleep(0, result=["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
    )
    monkeypatch.setattr(
        TradingBotConnector,
        "fetch_active_positions",
        lambda self: asyncio.sleep(
            0,
            result=[{
                "symbol": "BTCUSDT",
                "side": "LONG",
                "size": 0.5,
                "current_price": 65000.0,
                "margin": 5000.0,
                "unrealized_pnl": 200.0,
            }],
        ),
    )

    payload = await calculate_portfolio_risk_payload(obs_repo=None, window_days=14)
    assert payload["prediction_is_not_authorization"] is True
    assert "portfolio_summary" in payload

    cached = get_latest_portfolio_risk()
    assert cached is not None
    assert cached["timestamp"] == payload["timestamp"]


@pytest.mark.asyncio
async def test_sse_instant_risk_snapshot_on_connect():
    """Verify SSE connection yields an immediate risk_portfolio_update event."""
    from unittest.mock import AsyncMock, MagicMock

    from futuris.api.routers import risk
    from futuris.api.routers.streaming import sse_forecast_events

    # Set cached snapshot directly
    risk._LATEST_PORTFOLIO_RISK = {
        "active_pairs": ["BTCUSDT"],
        "portfolio_summary": {"overall_traffic_light": "green"},
        "prediction_is_not_authorization": True,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    mock_request = MagicMock()
    mock_request.is_disconnected = AsyncMock(return_value=False)

    resp = await sse_forecast_events(request=mock_request, target=None)
    assert resp.status_code == 200
    gen = resp.body_iterator

    chunk1 = await gen.__anext__()
    chunk2 = await gen.__anext__()

    assert b"event: connected" in chunk1
    assert b"event: risk_portfolio_update" in chunk2
    assert b"\"overall_traffic_light\": \"green\"" in chunk2

    await gen.aclose()
