"""Comprehensive tests for evaluation metrics, calibration analysis, shrinkage, and backtesting."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from futuris.core.enums import ConfidenceLevel
from futuris.evaluation.backtest import BacktestEngine
from futuris.evaluation.calibration import CalibrationAnalyzer
from futuris.evaluation.confidence import ConfidenceAssessor
from futuris.evaluation.drift import DriftMonitor
from futuris.evaluation.metrics import (
    brier_score,
    calibration_error,
    interval_coverage,
    interval_width,
    mae,
    mape,
    ranking_precision_recall,
    rmse,
)


def test_metrics_hand_computed_correctness():
    """Verify statistical metrics against analytical hand-computed values."""
    actual = [10.0, 20.0, 30.0]
    predicted = [12.0, 18.0, 33.0]

    # MAE = (|2| + |2| + |3|) / 3 = 7 / 3 = 2.3333...
    assert np.isclose(mae(actual, predicted), 7.0 / 3.0)

    # RMSE = sqrt((4 + 4 + 9) / 3) = sqrt(17 / 3) = 2.380476...
    assert np.isclose(rmse(actual, predicted), np.sqrt(17.0 / 3.0))

    # MAPE = (2/10 + 2/20 + 3/30)/3 * 100 = (0.2 + 0.1 + 0.1)/3 * 100 = 13.3333%
    assert np.isclose(mape(actual, predicted), (0.4 / 3.0) * 100.0)

    # Brier Score
    assert np.isclose(brier_score([True, False], [0.8, 0.2]), 0.04)

    # Interval metrics
    lows = [8.0, 15.0, 25.0]
    highs = [12.0, 25.0, 35.0]
    assert interval_coverage(actual, lows, highs) == 1.0
    assert np.isclose(interval_width(lows, highs), (4.0 + 10.0 + 10.0) / 3.0)

    # Precision and recall
    prec, rec = ranking_precision_recall(
        [True, False, True, False], [0.9, 0.8, 0.1, 0.2], cutoff=0.5
    )
    assert prec == 0.5
    assert rec == 0.5


def test_calibration_analyzer_detects_deliberate_miscalibration():
    """Verify that an intentionally miscalibrated model (70% vs 50% true rate) is flagged."""
    rng = np.random.default_rng(42)
    n = 1000
    probs = [0.70] * n
    actuals = [bool(b) for b in rng.binomial(1, 0.50, size=n)]

    ece = calibration_error(actuals, probs, num_bins=10)
    assert 0.15 < ece < 0.25

    analyzer = CalibrationAnalyzer()
    curve = analyzer.compute_reliability_curve(probs, actuals)
    non_empty = [
        f for f, c in zip(curve.observed_frequencies, curve.bin_counts, strict=True) if c > 0
    ]
    assert len(non_empty) == 1
    assert np.isclose(non_empty[0], 0.50, atol=0.05)


def test_hierarchical_shrinkage_behavior():
    """Verify shrinkage behavior: small n stays near pooled; large n converges to target."""
    analyzer = CalibrationAnalyzer(shrinkage_k=20.0)
    pooled_rate = 0.10
    target_rate = 0.80

    # 1. Small sample (n = 3) -> Shrunk rate stays close to pooled
    shrunk_small = analyzer.apply_hierarchical_shrinkage(
        n_target=3, target_observed_rate=target_rate, pooled_observed_rate=pooled_rate, k=20.0
    )
    assert np.isclose(shrunk_small, 4.4 / 23.0)
    assert shrunk_small < 0.25

    # 2. Large sample (n = 200) -> Shrunk rate converges near target
    shrunk_large = analyzer.apply_hierarchical_shrinkage(
        n_target=200, target_observed_rate=target_rate, pooled_observed_rate=pooled_rate, k=20.0
    )
    assert np.isclose(shrunk_large, 162.0 / 220.0)
    assert shrunk_large > 0.70


def test_confidence_assessor_rule_branches():
    """Verify confidence level transitions based on historical depth and degradation."""
    assessor = ConfidenceAssessor()

    # Rule 2: Sparse history -> LOW
    res_sparse = assessor.evaluate(
        historical_resolved_count=5,
        backtest_sample_size=50,
        long_run_mae=10.0,
        recent_30d_mae=10.0,
    )
    assert res_sparse.level == ConfidenceLevel.LOW

    # Rule 2: Moderate history -> MEDIUM
    res_med = assessor.evaluate(
        historical_resolved_count=30,
        backtest_sample_size=50,
        long_run_mae=10.0,
        recent_30d_mae=10.0,
    )
    assert res_med.level == ConfidenceLevel.MEDIUM

    # Rule 2 + 3: Rich history with sufficient backtest -> HIGH
    res_high = assessor.evaluate(
        historical_resolved_count=100,
        backtest_sample_size=50,
        long_run_mae=10.0,
        recent_30d_mae=10.0,
    )
    assert res_high.level == ConfidenceLevel.HIGH

    # Rule 4: Recent degradation (>20% degradation) degrades HIGH -> MEDIUM
    res_degraded = assessor.evaluate(
        historical_resolved_count=100,
        backtest_sample_size=50,
        long_run_mae=10.0,
        recent_30d_mae=13.5,
    )
    assert res_degraded.level == ConfidenceLevel.MEDIUM
    assert res_degraded.recent_degradation_detected is True


def test_drift_monitor_control_limits():
    """Verify DriftMonitor flags 3-sigma degradation."""
    monitor = DriftMonitor()
    historical_scores = [10.0, 10.2, 9.8, 10.1, 9.9, 10.0, 10.1, 9.9]

    # Normal scores within 3-sigma
    status_ok = monitor.evaluate_drift("model:v1", historical_scores, [10.2])
    assert status_ok.is_degraded is False

    # Severe degradation (> 3-sigma)
    status_degraded = monitor.evaluate_drift("model:v1", historical_scores, [25.0])
    assert status_degraded.is_degraded is True


@pytest.mark.asyncio
async def test_backtest_engine_run_on_synthetic_data():
    """Verify walk-forward backtest produces BacktestReport with sensible metrics."""
    backtester = BacktestEngine()
    start = datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=5)

    report = await backtester.run_backtest(
        target="service:checkout:capacity_exceedance_24h",
        start_date=start,
        end_date=end,
        stride_hours=24,
        horizon=timedelta(hours=24),
    )

    assert report.total_forecasts > 0
    assert "24h" in report.metrics_by_horizon
    h24 = report.metrics_by_horizon["24h"]
    assert h24.mae > 0.0
    assert 0.0 <= h24.interval_coverage <= 1.0
    assert len(report.calibration_curves) > 0
