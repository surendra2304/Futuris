"""Unit tests for GARCH(1,1) volatility clustering and heteroskedasticity forecaster."""

import math

import numpy as np
import pandas as pd
import pytest

from futuris.models.base import ModelAdapter
from futuris.models.registry import model_registry
from futuris.models.volatility_forecaster import GARCHVolatilityModelAdapter


def test_garch_model_protocol_and_registration():
    """Verify GARCHVolatilityModelAdapter conforms to ModelAdapter protocol and is registered."""
    adapter = model_registry.get_adapter("garch_volatility")
    assert isinstance(adapter, ModelAdapter)
    assert adapter.name == "garch_volatility"
    assert len(adapter.get_config_hash()) == 16


def test_garch_volatility_fitting_and_stationarity():
    """Verify GARCH fitting discovers volatility persistence and stationarity."""
    # Synthesize time series with clear ARCH/GARCH volatility clustering
    np.random.seed(42)
    n = 100
    eps = np.zeros(n)
    sigma2 = np.zeros(n)
    sigma2[0] = 1.0

    omega_true = 0.05
    alpha_true = 0.15
    beta_true = 0.80

    for t in range(1, n):
        sigma2[t] = omega_true + alpha_true * (eps[t - 1] ** 2) + beta_true * sigma2[t - 1]
        eps[t] = np.random.normal(0, math.sqrt(sigma2[t]))

    dates = pd.date_range("2026-03-01", periods=n, freq="D", tz="UTC")
    y = pd.Series(eps, index=dates)
    x = pd.DataFrame(index=dates)
    as_of = dates[-1].to_pydatetime()

    adapter = GARCHVolatilityModelAdapter()
    adapter.fit(x, y, as_of)

    assert adapter.fitted is True
    assert adapter.omega > 0.0
    assert 0.01 <= adapter.alpha <= 0.40
    assert 0.40 <= adapter.beta <= 0.95
    assert adapter.persistence < 1.0
    assert adapter.unconditional_variance > 0.0
    assert adapter.half_life_steps > 0.0


def test_garch_prediction_intervals_and_regimes():
    """Verify multi-step forward volatility forecasts and regime classification."""
    np.random.seed(123)
    n = 50
    # Simulate high volatility shock at the end
    vals = np.random.normal(0, 1.0, n)
    vals[-5:] = np.random.normal(0, 5.0, 5)

    dates = pd.date_range("2026-04-01", periods=n, freq="h", tz="UTC")
    y = pd.Series(vals, index=dates)
    x = pd.DataFrame(index=dates)
    as_of = dates[-1].to_pydatetime()

    adapter = GARCHVolatilityModelAdapter()
    adapter.fit(x, y, as_of)

    pred = adapter.predict(horizon=8, capacity_threshold=6.0)

    assert len(pred.point_forecast) == 8
    assert len(pred.intervals) == 8
    assert pred.range_lower <= pred.range_upper

    for interval in pred.intervals:
        assert interval.lower <= interval.central <= interval.upper

    # Check regime metadata
    assert "volatility_regime" in pred.metadata
    assert pred.metadata["volatility_regime"] in [
        "high_volatility",
        "normal_volatility",
        "low_volatility",
    ]
    assert pred.metadata["persistence"] < 1.0
    assert pred.metadata["current_conditional_volatility"] > 0.0


def test_garch_unfitted_predict_raises():
    """Verify calling predict on unfitted GARCH adapter raises ValueError."""
    adapter = GARCHVolatilityModelAdapter()
    with pytest.raises(ValueError, match="fitted before predict"):
        adapter.predict(horizon=5)
