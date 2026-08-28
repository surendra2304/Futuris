"""Comprehensive tests for statsforecast adapters, routing, and ForecastEngine."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from futuris.connectors.synthetic_telemetry import SyntheticTelemetryConnector
from futuris.core.engine import ForecastEngine
from futuris.evidence.snapshots import EvidenceSnapshotter
from futuris.models.adapters import NaiveAdapter, SeasonalNaiveAdapter
from futuris.models.base import calculate_exceedance_probability
from futuris.models.routing import ModelRouter, SeriesMetadata


def test_seasonal_naive_beats_naive_on_seasonal_data():
    """Verify that SeasonalNaive achieves lower MAE than Naive on a periodic series."""
    t0 = datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)
    steps = 288 * 5  # 5 days
    time_index = [t0 + timedelta(minutes=5 * i) for i in range(steps)]

    y_vals = 1000.0 + 500.0 * np.sin(2 * np.pi * np.arange(steps) / 288.0)
    y_series = pd.Series(y_vals, index=time_index)
    x_df = pd.DataFrame(index=time_index)

    train_slice = y_series.iloc[:-288]
    test_slice = y_series.iloc[-288:].to_numpy()

    snaive = SeasonalNaiveAdapter(season_length=288)
    naive = NaiveAdapter()

    snaive.fit(x_df.iloc[:-288], train_slice, as_of=time_index[-289])
    naive.fit(x_df.iloc[:-288], train_slice, as_of=time_index[-289])

    p_snaive = snaive.predict(288)
    p_naive = naive.predict(288)

    mae_snaive = float(np.mean(np.abs(np.array(p_snaive.point_forecast) - test_slice)))
    mae_naive = float(np.mean(np.abs(np.array(p_naive.point_forecast) - test_slice)))

    assert mae_snaive < mae_naive


def test_exceedance_probability_sanity():
    """Verify exceedance probability boundaries for low vs high trending demand."""
    residuals = np.random.default_rng(42).normal(0, 50, size=500)

    # 1. Low demand series (far below 4000)
    low_demand = np.array([1200.0, 1250.0, 1300.0, 1280.0])
    p_low_emp = calculate_exceedance_probability(
        low_demand, residuals, capacity_threshold=4000.0, method="empirical"
    )
    p_low_norm = calculate_exceedance_probability(
        low_demand, residuals, capacity_threshold=4000.0, method="normal"
    )
    assert p_low_emp < 0.05
    assert p_low_norm < 0.05

    # 2. Trending high demand series (crossing 4000)
    high_demand = np.array([3950.0, 4050.0, 4100.0, 4200.0])
    p_high_emp = calculate_exceedance_probability(
        high_demand, residuals, capacity_threshold=4000.0, method="empirical"
    )
    p_high_norm = calculate_exceedance_probability(
        high_demand, residuals, capacity_threshold=4000.0, method="normal"
    )
    assert p_high_emp > 0.50
    assert p_high_norm > 0.50


def test_router_heuristics_policy_branches():
    """Verify deterministic router selection across all documented heuristic policies."""
    router = ModelRouter()

    # Short history (< 288)
    meta_short = SeriesMetadata(history_points=100, frequency_minutes=5)
    assert router.select_candidates(meta_short, horizon_steps=24) == ["drift", "naive"]

    # Rich history (>= 2016) with weekly seasonality
    meta_rich = SeriesMetadata(
        history_points=2500, frequency_minutes=5, has_weekly_seasonality=True
    )
    assert router.select_candidates(meta_rich, horizon_steps=288) == [
        "mean_ensemble", "auto_ets", "seasonal_naive", "drift", "naive"
    ]

    # Moderate history (288 to 2015)
    meta_moderate = SeriesMetadata(history_points=500, frequency_minutes=5)
    assert router.select_candidates(meta_moderate, horizon_steps=24) == [
        "seasonal_naive", "auto_ets", "drift", "naive"
    ]


@pytest.mark.asyncio
async def test_forecast_engine_end_to_end_and_reproducibility(tmp_path):
    """Verify end-to-end orchestration and strict byte reproducibility across runs."""
    connector = SyntheticTelemetryConnector(seed=42)
    as_of = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)
    horizon = timedelta(hours=24)

    # First run
    snap1 = EvidenceSnapshotter(base_storage_path=str(tmp_path / "run1"))
    engine1 = ForecastEngine(connector=connector, snapshotter=snap1)
    results1 = await engine1.orchestrate(
        target="service:checkout:capacity_exceedance_24h",
        as_of=as_of,
        horizon=horizon,
        capacity_threshold=4000.0,
        history_lookback_days=7,
    )
    assert len(results1) == 1
    f1 = results1[0]
    assert f1.target == "service:checkout:capacity_exceedance_24h"
    assert f1.prediction > 0
    assert f1.range_lower <= f1.prediction <= f1.range_upper
    assert f1.probability is not None
    assert 0.0 <= f1.probability <= 1.0

    # Second run with identical inputs
    snap2 = EvidenceSnapshotter(base_storage_path=str(tmp_path / "run2"))
    engine2 = ForecastEngine(connector=connector, snapshotter=snap2)
    results2 = await engine2.orchestrate(
        target="service:checkout:capacity_exceedance_24h",
        as_of=as_of,
        horizon=horizon,
        capacity_threshold=4000.0,
        history_lookback_days=7,
    )
    assert len(results2) == 1
    f2 = results2[0]

    # Assert mathematical and prediction values are strictly identical
    assert f1.prediction == f2.prediction
    assert f1.range_lower == f2.range_lower
    assert f1.range_upper == f2.range_upper
    assert f1.probability == f2.probability
    assert f1.model_version == f2.model_version
    assert len(f1.drivers) == len(f2.drivers)
    assert f1.drivers[0].name == f2.drivers[0].name
    assert f1.drivers[0].strength == f2.drivers[0].strength
