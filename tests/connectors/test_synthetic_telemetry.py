"""Tests for synthetic telemetry generator determinism and data generation."""

from datetime import UTC, datetime, timedelta

import pytest

from futuris.connectors.synthetic_telemetry import SyntheticTelemetryConnector


@pytest.mark.asyncio
async def test_synthetic_telemetry_determinism():
    """Verify that same seed produces identical observation values and timestamps."""
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=30)

    gen1 = SyntheticTelemetryConnector(seed=123)
    gen2 = SyntheticTelemetryConnector(seed=123)

    series1 = await gen1.fetch(start, end)
    series2 = await gen2.fetch(start, end)

    assert len(series1) == len(series2)
    assert len(series1) > 0

    for obs1, obs2 in zip(series1, series2, strict=True):
        assert obs1.observed_at == obs2.observed_at
        assert obs1.value == obs2.value
        assert obs1.series_id == obs2.series_id
        assert obs1.unit == obs2.unit


@pytest.mark.asyncio
async def test_synthetic_telemetry_180_days_generation():
    """Verify generator scales cleanly over 180+ days at 5-minute resolution."""
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=180)

    gen = SyntheticTelemetryConnector(seed=42)
    series = await gen.fetch(start, end)

    expected_steps = (180 * 24 * 12) + 1  # 51,841 points
    assert len(series) == expected_steps
    assert series[0].series_id == "checkout:requests_per_minute"
    assert all(obs.value > 0 for obs in series)
    assert series[-1].tags["capacity_limit"] == 5000.0  # upgraded after 90 days
