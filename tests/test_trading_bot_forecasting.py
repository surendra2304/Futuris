"""Tests for TradingBotConnector and MarketRegimeForecaster."""

from datetime import UTC, datetime

import httpx
import pytest

from futuris.connectors.trading_bot import (
    TARGET_VOLATILITY_SPIKE,
    TradingBotConnector,
)
from futuris.models.regime import MarketRegime, MarketRegimeForecaster


@pytest.mark.asyncio
async def test_trading_bot_connector_parsing():
    """Verify TradingBotConnector ingests and normalizes trading telemetry."""
    start = datetime(2026, 8, 28, 10, 0, 0, tzinfo=UTC)
    end = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)

    mock_telemetry = [
        {
            "timestamp": "2026-08-28T10:00:00Z",
            "metric_type": "equity",
            "value": 125000.50,
            "strategy": "trend_following_v2",
        },
        {
            "timestamp": "2026-08-28T10:05:00Z",
            "metric_type": "volatility",
            "symbol": "BTC",
            "value": 0.045,
        },
        {
            "timestamp": "2026-08-28T10:10:00Z",
            "metric_type": "drawdown",
            "value": 3.2,
        },
        {
            "timestamp": "2026-08-28T10:15:00Z",
            "metric_type": "position_exposure",
            "value": 0.65,
        },
    ]

    async def _handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" in request.headers
        return httpx.Response(200, json=mock_telemetry)

    transport = httpx.MockTransport(_handler)
    connector = TradingBotConnector(
        base_url="http://trading-bot.local",
        api_key="bot_secret_token",
        transport=transport,
    )

    observations = await connector.fetch(start, end)
    assert len(observations) == 4
    assert observations[0].series_id == "trading:equity"
    assert observations[0].value == 125000.50
    assert observations[1].series_id == "trading:volatility:btc"
    assert observations[2].series_id == "trading:drawdown"
    assert observations[3].series_id == "trading:position_exposure"
    assert TARGET_VOLATILITY_SPIKE == "trading:btc:volatility_spike_24h"


def test_market_regime_detection_and_routing():
    """Verify MarketRegimeForecaster accurately detects trending, volatile, and ranging regimes."""
    forecaster = MarketRegimeForecaster()

    # 1. Trending Series (strong upward slope)
    trending_prices = [100.0 + i * 2.5 for i in range(30)]
    res_trend = forecaster.detect_regime(trending_prices)
    assert res_trend.current_regime == MarketRegime.TRENDING
    assert forecaster.route_model_for_regime(res_trend.current_regime) == "auto_arima@v1"

    # 2. Volatile Series (spiking volatility)
    ranging_prices = [100.0, 101.0, 99.5, 100.5, 100.0, 99.8, 100.2] * 4
    vol_spikes = [0.01, 0.02, 0.015, 0.012, 0.085]  # last one spikes
    res_vol = forecaster.detect_regime(ranging_prices, volatilities=vol_spikes)
    assert res_vol.current_regime == MarketRegime.VOLATILE
    assert forecaster.route_model_for_regime(res_vol.current_regime) == "auto_ets@v1"

    # 3. Ranging Series (flat consolidation)
    flat_prices = [100.0, 100.2, 99.9, 100.1, 100.0, 99.8] * 5
    res_range = forecaster.detect_regime(flat_prices, volatilities=[0.01] * 5)
    assert res_range.current_regime == MarketRegime.RANGING
    assert forecaster.route_model_for_regime(res_range.current_regime) == "seasonal_naive@v1"
