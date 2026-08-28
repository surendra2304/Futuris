"""Benchmark suites, calibration metrics, backtesting, and distribution drift detection."""

from futuris.evaluation.backtest import BacktestEngine, BacktestReport, HorizonMetrics
from futuris.evaluation.calibration import CalibrationAnalyzer, ReliabilityCurve
from futuris.evaluation.confidence import (
    ConfidenceAssessmentResult,
    ConfidenceAssessor,
    confidence_assessor,
)
from futuris.evaluation.drift import DriftMonitor, DriftStatus
from futuris.evaluation.metrics import (
    brier_score,
    calibration_error,
    interval_coverage,
    interval_width,
    log_loss,
    mae,
    mape,
    ranking_precision_recall,
    rmse,
)

__all__ = [
    "BacktestEngine",
    "BacktestReport",
    "CalibrationAnalyzer",
    "ConfidenceAssessmentResult",
    "ConfidenceAssessor",
    "DriftMonitor",
    "DriftStatus",
    "HorizonMetrics",
    "ReliabilityCurve",
    "brier_score",
    "calibration_error",
    "confidence_assessor",
    "interval_coverage",
    "interval_width",
    "log_loss",
    "mae",
    "mape",
    "ranking_precision_recall",
    "rmse",
]
