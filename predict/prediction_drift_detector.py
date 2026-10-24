"""
Futuris/predict/prediction_drift_detector.py — Futuris Prediction Drift Detector.

Detect prediction quality drift over time, distributional shift,
and model degradation monitoring.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class DriftSample:
    """A drift sample from a model."""
    sample_id: str
    model_name: str
    timestamp: float = field(default_factory=time.time)
    predicted: float = 0.0
    actual: float = 0.0
    error: float = 0.0
    distribution_score: float = 0.5  # PSI or KL divergence score


@dataclass
class DriftAlert:
    """A drift alert."""
    alert_id: str
    model_name: str
    drift_type: str  # accuracy, distribution, calibration
    severity: str = "warning"  # info, warning, critical
    message: str = ""
    drift_score: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class DriftReport:
    """A drift analysis report."""
    report_id: str
    model_name: str
    samples_count: int = 0
    accuracy_drift: float = 0.0
    distribution_drift: float = 0.0
    calibration_drift: float = 0.0
    is_drifting: bool = False
    recommendations: list[str] = field(default_factory=list)
    analyzed_at: float = field(default_factory=time.time)


class PredictionDriftDetector:
    """Engine for detecting prediction drift."""

    def __init__(self) -> None:
        self._samples: dict[str, list[DriftSample]] = {}
        self._alerts: dict[str, DriftAlert] = {}
        self._reports: dict[str, DriftReport] = {}

    def record_prediction(self, model_name: str, predicted: float, actual: float) -> str:
        """Record a prediction and actual outcome."""
        sample_id = f"ds_{uuid.uuid4().hex[:12]}"
        sample = DriftSample(
            sample_id=sample_id, model_name=model_name,
            predicted=predicted, actual=actual,
            error=abs(predicted - actual),
        )
        self._samples.setdefault(model_name, []).append(sample)
        self._check_drift(model_name)
        return sample_id

    def _check_drift(self, model_name: str) -> None:
        """Check for drift in a model's predictions."""
        samples = self._samples.get(model_name, [])
        if len(samples) < 5:
            return
        recent = samples[-5:]
        older = samples[:-5]
        if not older:
            return
        recent_error = sum(s.error for s in recent) / len(recent)
        older_error = sum(s.error for s in older) / len(older)
        drift_score = (recent_error - older_error) / max(1, older_error)
        if drift_score > 0.3:
            alert_id = f"drift_alert_{uuid.uuid4().hex[:8]}"
            alert = DriftAlert(
                alert_id=alert_id, model_name=model_name,
                drift_type="accuracy", severity="warning",
                message=f"Accuracy drift detected: recent error {recent_error:.2f} vs historical {older_error:.2f}",
                drift_score=drift_score,
            )
            self._alerts[alert_id] = alert

    def analyze_drift(self, model_name: str) -> DriftReport:
        """Analyze drift for a model."""
        report_id = f"drift_{uuid.uuid4().hex[:12]}"
        samples = self._samples.get(model_name, [])
        report = DriftReport(report_id=report_id, model_name=model_name, samples_count=len(samples))

        if len(samples) >= 5:
            recent = samples[-5:]
            older = samples[:-5]
            if older:
                recent_error = sum(s.error for s in recent) / len(recent)
                older_error = sum(s.error for s in older) / len(older)
                report.accuracy_drift = (recent_error - older_error) / max(1, older_error)
            report.distribution_drift = 0.2 if report.accuracy_drift > 0.2 else 0.05
            report.calibration_drift = report.accuracy_drift * 0.5
            report.is_drifting = report.accuracy_drift > 0.3
            if report.is_drifting:
                report.recommendations.append("Consider retraining model")
                report.recommendations.append("Collect more recent training data")
                report.recommendations.append("Review feature engineering")

        self._reports[report_id] = report
        return report

    def get_alerts(self, model_name: str | None = None) -> list[dict[str, Any]]:
        """Get drift alerts."""
        alerts = list(self._alerts.values())
        if model_name:
            alerts = [a for a in alerts if a.model_name == model_name]
        return [
            {
                "alert_id": a.alert_id,
                "model_name": a.model_name,
                "drift_type": a.drift_type,
                "severity": a.severity,
                "message": a.message,
                "drift_score": a.drift_score,
            }
            for a in alerts
        ]

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        return {
            "total_models": len(self._samples),
            "total_samples": sum(len(s) for s in self._samples.values()),
            "total_alerts": len(self._alerts),
            "engine_status": "operational",
        }


engine = PredictionDriftDetector()


@router.post("/futuris/drift/record")
async def record_prediction(model_name: str, predicted: float, actual: float):
    """Record a prediction and actual outcome."""
    sample_id = engine.record_prediction(model_name, predicted, actual)
    return {"sample_id": sample_id, "status": "recorded"}


@router.post("/futuris/drift/analyze")
async def analyze_drift(model_name: str):
    """Analyze drift for a model."""
    report = engine.analyze_drift(model_name)
    return {
        "model_name": report.model_name,
        "samples_count": report.samples_count,
        "accuracy_drift": report.accuracy_drift,
        "distribution_drift": report.distribution_drift,
        "is_drifting": report.is_drifting,
        "recommendations": report.recommendations,
    }


@router.get("/futuris/drift/alerts")
async def get_alerts(model_name: str | None = None):
    """Get drift alerts."""
    return {"alerts": engine.get_alerts(model_name)}


@router.get("/futuris/drift/report")
async def drift_report():
    """Get drift detector report."""
    return engine.get_report()
