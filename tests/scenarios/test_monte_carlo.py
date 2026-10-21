"""Unit tests for MonteCarloScenarioEngine."""

import numpy as np
import pytest

from futuris.scenarios.monte_carlo import (
    DriverDistribution,
    MonteCarloScenarioEngine,
)


@pytest.fixture
def mc_engine() -> MonteCarloScenarioEngine:
    return MonteCarloScenarioEngine(seed=42)


def test_sample_correlated_drivers_cholesky(mc_engine: MonteCarloScenarioEngine):
    """Verify correlated drivers sample matches prescribed correlation structure."""
    drivers = [
        DriverDistribution(name="demand_growth", dist_type="normal", param1=0.05, param2=0.02),
        DriverDistribution(name="compute_cost", dist_type="lognormal", param1=0.0, param2=0.2),
        DriverDistribution(name="conversion_rate", dist_type="uniform", param1=0.02, param2=0.08),
    ]

    # Prescribe strong positive correlation between demand and compute_cost
    corr_matrix = np.array([
        [1.0, 0.75, 0.0],
        [0.75, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ])

    samples = mc_engine.sample_correlated_drivers(
        drivers=drivers,
        correlation_matrix=corr_matrix,
        num_samples=3000,
        use_antithetic_variates=True,
    )

    assert "demand_growth" in samples
    assert "compute_cost" in samples
    assert "conversion_rate" in samples

    # Empirical correlation between demand and cost should be close to 0.75
    emp_r = float(np.corrcoef(samples["demand_growth"], samples["compute_cost"])[0, 1])
    assert 0.65 <= emp_r <= 0.85

    # Uniform driver within [0.02, 0.08]
    assert np.all(samples["conversion_rate"] >= 0.019)
    assert np.all(samples["conversion_rate"] <= 0.081)


def test_simulate_trajectories_fan_chart_and_exceedance(mc_engine: MonteCarloScenarioEngine):
    """Verify trajectory propagation and fan chart percentile ordering."""
    drivers = [
        DriverDistribution(name="shock", dist_type="normal", param1=1.02, param2=0.04),
    ]

    # Compound growth transfer function: y_{t+1} = y_t * shock
    def growth_transfer(drivers_dict: dict[str, float], prev_val: float, step: int) -> float:
        _ = step
        return prev_val * drivers_dict["shock"]

    result = mc_engine.simulate_trajectories(
        base_value=100.0,
        drivers=drivers,
        transfer_function=growth_transfer,
        horizon_steps=8,
        num_samples=2000,
        capacity_threshold=115.0,
        use_antithetic_variates=True,
    )

    assert result.horizon_steps == 8
    assert len(result.fan_chart) == 8
    assert len(result.trajectories_sample) == 5

    for slice_item in result.fan_chart:
        assert (
            slice_item.p05
            <= slice_item.p10
            <= slice_item.p25
            <= slice_item.p50
            <= slice_item.p75
            <= slice_item.p90
            <= slice_item.p95
            <= slice_item.p99
        )
        assert slice_item.exceedance_probability is not None
        assert 0.0 <= slice_item.exceedance_probability <= 1.0

    # Trend should show upward drift over time
    assert result.fan_chart[-1].p50 > result.fan_chart[0].p50
