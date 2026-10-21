"""Tests for TradingBotConnector market ticker series fetching."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from futuris.connectors.trading_bot import (
    TARGET_BTC_PRICE_VARIANCE,
    TARGET_ETH_PRICE_VARIANCE,
    TARGET_SOL_PRICE_VARIANCE,
    TradingBotConnector,
)


@pytest.mark.asyncio
async def test_trading_bot_connector_defaults():
    """Verify connector defaults to STRATEX_URL and valid credential handling."""
    connector = TradingBotConnector()
    assert "stratex" in connector.base_url or "trading-bot" in connector.base_url
    assert TARGET_BTC_PRICE_VARIANCE == "trading:BTCUSDT:price_variance_24h"
    assert TARGET_ETH_PRICE_VARIANCE == "trading:ETHUSDT:price_variance_24h"
    assert TARGET_SOL_PRICE_VARIANCE == "trading:SOLUSDT:price_variance_24h"


@pytest.mark.asyncio
async def test_fetch_ticker_series_success():
    """Verify fetch_ticker_series maps remote JSON data to Observation domain objects."""
    now = datetime.now(UTC)
    mock_payload = [
        {
            "timestamp": (now - timedelta(hours=i)).isoformat(),
            "price": 60000.0 + i * 100.0,
            "metric_type": "price",
            "unit": "USD",
        }
        for i in range(5)
    ]

    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            assert "telemetry/market/BTCUSDT" in str(request.url)
            return httpx.Response(200, json=mock_payload)

    connector = TradingBotConnector(
        base_url="http://test-stratex:8000",
        api_key="test_key",
        transport=MockTransport(),
    )

    obs = await connector.fetch_ticker_series("BTCUSDT", now - timedelta(days=1), now)
    assert len(obs) == 5
    assert obs[0].series_id == "trading:BTCUSDT:price"
    assert obs[0].unit == "USD"
    assert obs[0].source == "stratex:BTCUSDT"


@pytest.mark.asyncio
async def test_fetch_ticker_series_fallback_to_trading_telemetry():
    """Verify 404 on market/SYMBOL falls back to /telemetry/trading."""
    now = datetime.now(UTC)
    mock_payload = [
        {
            "timestamp": now.isoformat(),
            "value": 3200.0,
            "metric_type": "price",
        }
    ]

    class FallbackTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if "telemetry/market" in str(request.url):
                return httpx.Response(404)
            return httpx.Response(200, json=mock_payload)

    connector = TradingBotConnector(
        base_url="http://test-stratex:8000",
        api_key="test_key",
        transport=FallbackTransport(),
    )

    obs = await connector.fetch_ticker_series("ETHUSDT", now - timedelta(days=1), now)
    assert len(obs) == 1
    assert obs[0].value == 3200.0


@pytest.mark.asyncio
async def test_fetch_ticker_series_connection_error_graceful():
    """Verify connection failure returns empty list without raising."""

    class ErrorTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Stratex node unreachable")

    connector = TradingBotConnector(
        base_url="http://offline-stratex:8000",
        transport=ErrorTransport(),
    )

    now = datetime.now(UTC)
    obs = await connector.fetch_ticker_series("SOLUSDT", now - timedelta(days=1), now)
    assert obs == []
