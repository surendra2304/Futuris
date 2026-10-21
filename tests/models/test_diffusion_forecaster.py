"""Unit tests for Bass diffusion and Hawkes process cascade forecasters."""

import numpy as np
import pandas as pd
import pytest

from futuris.models.base import ModelAdapter
from futuris.models.diffusion_forecaster import (
    BassDiffusionModelAdapter,
    HawkesProcessCascadeAdapter,
)
from futuris.models.registry import model_registry


def test_bass_diffusion_protocol_and_registry():
    """Verify Bass model implements ModelAdapter protocol and is registered."""
    adapter = model_registry.get_adapter("bass_diffusion")
    assert isinstance(adapter, ModelAdapter)
    assert adapter.name == "bass_diffusion"
    assert len(adapter.get_config_hash()) == 16


def test_bass_diffusion_fitting_and_prediction():
    """Verify Bass diffusion model estimates adoption curve and produces valid intervals."""
    # Create realistic bell-shaped adoption curve
    n = 30
    t = np.arange(1, n + 1)
    # Target: m=5000, p=0.03, q=0.38
    exp_t = np.exp(-(0.03 + 0.38) * t)
    cum = 5000.0 * (1.0 - exp_t) / (1.0 + (0.38 / 0.03) * exp_t)
    rates = np.diff(np.insert(cum, 0, 0.0))
    # Add slight noise
    np.random.seed(42)
    noisy_rates = np.maximum(0.0, rates + np.random.normal(0, 5, n))

    dates = pd.date_range("2026-01-01", periods=n, freq="D", tz="UTC")
    y = pd.Series(noisy_rates, index=dates)
    x = pd.DataFrame(index=dates)
    as_of = dates[-1].to_pydatetime()

    adapter = BassDiffusionModelAdapter()
    adapter.fit(x, y, as_of)

    assert adapter.fitted is True
    assert adapter.m > 1000.0
    assert 0.001 <= adapter.p <= 0.20
    assert 0.05 <= adapter.q <= 1.5

    # Predict next 10 days
    pred = adapter.predict(horizon=10, capacity_threshold=200.0)

    assert len(pred.point_forecast) == 10
    assert len(pred.intervals) == 10
    assert pred.central_estimate > 0.0
    assert pred.range_lower <= pred.central_estimate <= pred.range_upper

    for interval in pred.intervals:
        assert interval.lower <= interval.central <= interval.upper

    assert "theoretical_peak_step" in pred.metadata
    assert "market_potential_M" in pred.metadata
    assert pred.metadata["p_innovation"] > 0
    assert pred.metadata["q_imitation_virality"] > 0


def test_hawkes_cascade_protocol_and_registry():
    """Verify Hawkes process cascade adapter implements ModelAdapter protocol."""
    adapter = model_registry.get_adapter("hawkes_cascade")
    assert isinstance(adapter, ModelAdapter)
    assert adapter.name == "hawkes_cascade"
    assert len(adapter.get_config_hash()) == 16


def test_hawkes_cascade_fitting_and_branching_ratio():
    """Verify Hawkes process fits self-exciting point process dynamics."""
    # Synthesize bursty cascade event sequence
    n = 25
    np.random.seed(42)
    # Baseline + burst at step 10
    burst_series = np.ones(n) * 5.0
    burst_series[10:14] += np.array([45.0, 30.0, 18.0, 8.0])

    dates = pd.date_range("2026-02-01", periods=n, freq="h", tz="UTC")
    y = pd.Series(burst_series, index=dates)
    x = pd.DataFrame(index=dates)
    as_of = dates[-1].to_pydatetime()

    adapter = HawkesProcessCascadeAdapter()
    adapter.fit(x, y, as_of)

    assert adapter.fitted is True
    assert adapter.alpha > 0.0
    assert adapter.beta > 0.0
    assert 0.0 < adapter.branching_ratio < 2.0

    pred = adapter.predict(horizon=6, capacity_threshold=15.0)

    assert len(pred.point_forecast) == 6
    assert len(pred.intervals) == 6
    assert pred.range_lower <= pred.range_upper

    for interval in pred.intervals:
        assert interval.lower <= interval.central <= interval.upper

    assert "branching_ratio" in pred.metadata
    assert pred.metadata["cascade_regime"] in ["subcritical_stable", "supercritical"]
    assert "baseline_mu" in pred.metadata


def test_models_unfitted_predict_raises():
    """Verify calling predict on unfitted model raises ValueError."""
    bass = BassDiffusionModelAdapter()
    with pytest.raises(ValueError, match="fitted before predict"):
        bass.predict(horizon=5)

    hawkes = HawkesProcessCascadeAdapter()
    with pytest.raises(ValueError, match="fitted before predict"):
        hawkes.predict(horizon=5)
