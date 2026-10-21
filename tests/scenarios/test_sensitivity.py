"""Unit tests for GlobalSensitivityAnalyzer (Sobol variance indices and Tornado ranking)."""

import pytest

from futuris.scenarios.sensitivity import GlobalSensitivityAnalyzer


@pytest.fixture
def analyzer() -> GlobalSensitivityAnalyzer:
    return GlobalSensitivityAnalyzer(seed=42)


def test_sobol_indices_linear_ranking(analyzer: GlobalSensitivityAnalyzer):
    """Verify first-order Sobol index correctly assigns dominant variance to larger coefficient."""
    # Model: Y = 4.0 * X1 + 1.0 * X2
    def linear_model(drivers: dict[str, float]) -> float:
        return 4.0 * drivers["x1"] + 1.0 * drivers["x2"]

    ranges = {
        "x1": (0.0, 10.0),
        "x2": (0.0, 10.0),
    }

    sobol = analyzer.compute_sobol_indices(ranges, linear_model, num_samples=1500)

    assert len(sobol) == 2
    # x1 should dominate variance (4^2 = 16 vs 1^2 = 1)
    s_x1 = next(s for s in sobol if s.driver_name == "x1")
    s_x2 = next(s for s in sobol if s.driver_name == "x2")

    assert s_x1.first_order > s_x2.first_order
    assert s_x1.first_order > 0.80
    assert s_x2.first_order < 0.20


def test_sobol_indices_interaction_detection(analyzer: GlobalSensitivityAnalyzer):
    """Verify Sobol total-order index detects non-linear multiplicative interaction."""
    # Model with pure interaction: Y = X1 * X2
    def interaction_model(drivers: dict[str, float]) -> float:
        return drivers["a"] * drivers["b"]

    ranges = {
        "a": (1.0, 5.0),
        "b": (1.0, 5.0),
    }

    sobol = analyzer.compute_sobol_indices(ranges, interaction_model, num_samples=1500)

    for s in sobol:
        # Total order must exceed first order when interaction is present
        assert s.total_order >= s.first_order
        assert s.interaction_effect >= 0.0


def test_tornado_ranking(analyzer: GlobalSensitivityAnalyzer):
    """Verify Tornado ranking sorts drivers by magnitude of swing."""
    def test_func(drivers: dict[str, float]) -> float:
        return 10.0 * drivers["alpha"] + 2.0 * drivers["beta"] + 0.1 * drivers["gamma"]

    base = {"alpha": 50.0, "beta": 50.0, "gamma": 50.0}

    bars = analyzer.compute_tornado_ranking(base, test_func, swing_pct=0.20)

    assert len(bars) == 3
    assert bars[0].driver_name == "alpha"
    assert bars[1].driver_name == "beta"
    assert bars[2].driver_name == "gamma"
    assert bars[0].swing_range > bars[1].swing_range > bars[2].swing_range


def test_full_global_sensitivity_analyze(analyzer: GlobalSensitivityAnalyzer):
    """Verify end-to-end global sensitivity study produces complete report."""
    def target_model(d: dict[str, float]) -> float:
        return d["users"] * d["retention"] * (1.0 - d["churn"])

    ranges = {
        "users": (1000.0, 5000.0),
        "retention": (0.4, 0.9),
        "churn": (0.01, 0.10),
    }
    base = {"users": 3000.0, "retention": 0.7, "churn": 0.05}

    report = analyzer.analyze(
        target_name="active_subscribers",
        driver_ranges=ranges,
        base_drivers=base,
        model_func=target_model,
        num_samples=1000,
        swing_pct=0.15,
    )

    assert report.target_name == "active_subscribers"
    assert report.total_variance > 0.0
    assert len(report.sobol_indices) == 3
    assert len(report.tornado_diagram) == 3
    assert len(report.top_drivers) > 0
