"""Tests for scheduled tracking targets and calibration sweep dispatching."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from futuris.core.enums import ConfidenceLevel, ForecastStatus
from futuris.core.schemas import Forecast
from futuris.infra.scheduler import ForecastScheduler, ForecastSubscription


def test_scheduler_default_subscriptions():
    """Verify default subscriptions include crypto and macro target series."""
    scheduler = ForecastScheduler()
    targets = [sub.target for sub in scheduler.subscriptions]

    # Verify primary market targets
    assert "trading:BTCUSDT:price_variance_24h" in targets
    assert "trading:ETHUSDT:price_variance_24h" in targets
    assert "trading:SOLUSDT:price_variance_24h" in targets

    # Verify macro/synthetic targets
    assert "macro:SPY:return_exceedance_24h" in targets
    assert "macro:DGS10:yield_shift_30d" in targets
    assert "macro:T10Y2Y:curve_inversion_30d" in targets
    assert "macro:UNRATE:unemployment_drift_90d" in targets
    assert "service:checkout:capacity_exceedance_24h" in targets


@pytest.mark.asyncio
async def test_scheduler_forecast_refresh_dispatches_friday_signal():
    """Verify high-conviction forecast refreshes trigger FRIDAY signal notification."""
    scheduler = ForecastScheduler()
    # Configure single subscription
    scheduler.subscriptions = [
        ForecastSubscription(target="trading:BTCUSDT:price_variance_24h")
    ]
    # Dynamic target discovery has its own contract; this test isolates one configured target.
    scheduler.sync_dynamic_trading_subscriptions = AsyncMock(return_value=[])

    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    mock_forecast = Forecast(
        target="trading:BTCUSDT:price_variance_24h",
        prediction=68500.0,
        range_lower=65000.0,
        range_upper=72000.0,
        probability=0.88,  # >= 0.70 threshold
        confidence=ConfidenceLevel.HIGH,
        model_version="test:v1.0",
        as_of=now,
        horizon=timedelta(hours=24),
        expires_at=now + timedelta(hours=24),
        review_at=now + timedelta(hours=6),
        status=ForecastStatus.ACTIVE,
    )

    from dataclasses import dataclass

    @dataclass
    class PipelineResult:
        forecast: Forecast

    scheduler.pipeline = AsyncMock()
    scheduler.pipeline.run.return_value = PipelineResult(forecast=mock_forecast)

    with patch(
        "futuris.ecosystem.signals.signal_dispatcher.notify_friday_forecast",
        new_callable=AsyncMock,
    ) as mock_dispatch:
        mock_dispatch.return_value = True
        refreshed = await scheduler.forecast_refresh_job()
        await asyncio.gather(*scheduler._in_flight_tasks)
        assert len(refreshed) == 1
        assert refreshed[0].prediction == 68500.0
        mock_dispatch.assert_awaited_once()
