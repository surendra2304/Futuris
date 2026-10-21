"""Unit tests for RiskCalibrationEngine (VaR, CVaR, Kupiec POF, Christoffersen independence)."""

import numpy as np
import pytest

from futuris.evaluation.risk_calibration import RiskCalibrationEngine


@pytest.fixture
def risk_engine() -> RiskCalibrationEngine:
    return RiskCalibrationEngine(significance_threshold=0.05)


def test_calculate_var_cvar_bounds(risk_engine: RiskCalibrationEngine):
    """Verify VaR and CVaR properties: CVaR >= VaR and breach rates match confidence."""
    np.random.seed(42)
    # 500 standard normal loss observations
    losses = np.random.normal(loc=0.0, scale=1.0, size=500)

    m95 = risk_engine.calculate_var_cvar(losses, confidence_level=0.95)
    assert m95.confidence_level == 0.95
    assert m95.cvar_expected_shortfall >= m95.var_historical
    assert 0.03 <= m95.breach_rate <= 0.07

    m99 = risk_engine.calculate_var_cvar(losses, confidence_level=0.99)
    assert m99.confidence_level == 0.99
    assert m99.var_historical >= m95.var_historical
    assert m99.cvar_expected_shortfall >= m99.var_historical
    assert 0.00 <= m99.breach_rate <= 0.03


def test_kupiec_pof_test_calibrated_vs_undercalibrated(risk_engine: RiskCalibrationEngine):
    """Verify Kupiec test accepts calibrated models and rejects severely under-calibrated ones."""
    # 1. Calibrated: 12 breaches out of 250 observations at 95% (expected 12.5)
    res_calibrated = risk_engine.kupiec_pof_test(
        breach_count=12,
        total_observations=250,
        confidence_level=0.95,
    )
    assert res_calibrated.is_rejected is False
    assert res_calibrated.p_value > 0.05

    # 2. Under-calibrated: 40 breaches out of 250 observations at 95% (16% vs 5%)
    res_rejected = risk_engine.kupiec_pof_test(
        breach_count=40,
        total_observations=250,
        confidence_level=0.95,
    )
    assert res_rejected.is_rejected is True
    assert res_rejected.p_value < 0.01


def test_christoffersen_independence_test_clustering(risk_engine: RiskCalibrationEngine):
    """Verify Christoffersen test detects consecutive breach clustering."""
    # Clustered breaches: all 8 breaches clustered consecutively
    clustered = [0] * 50 + [1, 1, 1, 1, 1, 1, 1, 1] + [0] * 50
    res_clustered = risk_engine.christoffersen_independence_test(clustered)
    assert res_clustered.is_rejected is True
    assert "clustering" in res_clustered.interpretation.lower()

    # Dispersed breaches: 8 breaches spread evenly
    dispersed = [0] * 12 + [1] + [0] * 12 + [1] + [0] * 12 + [1] + [0] * 12 + [1]
    res_dispersed = risk_engine.christoffersen_independence_test(dispersed)
    assert res_dispersed.is_rejected is False
    assert res_dispersed.p_value >= 0.05


def test_basel_traffic_light_zones(risk_engine: RiskCalibrationEngine):
    """Verify Basel traffic light zones (green for normal, red for severe failure)."""
    # 250 observations at 99% VaR:
    # 2 breaches -> green
    assert risk_engine.evaluate_traffic_light(2, 250, 0.99) == "green"
    # 6 breaches -> yellow
    assert risk_engine.evaluate_traffic_light(6, 250, 0.99) == "yellow"
    # 15 breaches -> red
    assert risk_engine.evaluate_traffic_light(15, 250, 0.99) == "red"


def test_full_risk_calibration_audit(risk_engine: RiskCalibrationEngine):
    """Verify end-to-end quantitative risk calibration report generation."""
    np.random.seed(42)
    residuals = np.random.normal(loc=0.0, scale=2.0, size=300)

    report = risk_engine.audit_risk_calibration(residuals)

    assert report.basel_traffic_light in ["green", "yellow", "red"]
    assert report.metrics_95.confidence_level == 0.95
    assert report.metrics_99.confidence_level == 0.99
    assert report.christoffersen_conditional_coverage.statistic >= 0.0
    assert "total_samples" in report.calibration_summary
    assert report.calibration_summary["total_samples"] == 300
