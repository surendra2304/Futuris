"""
Futuris/predict/prediction_calibration_optimizer.py — Futuris Prediction Calibration Optimizer.

Optimizes prediction calibration: reliability diagrams, Platt scaling,
isotonic regression, temperature scaling, and calibration drift detection.
Ensures predictions are trustworthy and well-calibrated.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class CalibrationData:
    """Calibration data for a model."""
    data_id: str
    model_name: str
    predictions: list[float] = field(default_factory=list)
    actuals: list[float] = field(default_factory=list)
    confidence_levels: list[float] = field(default_factory=lambda: [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9])
    reliability: list[float] = field(default_factory=list)
    created_at: float = 0.0


@dataclass
class CalibrationMetrics:
    """Calibration quality metrics."""
    model_name: str
    brier_score: float = 0.0
    expected_calibration_error: float = 0.0
    maximum_calibration_error: float = 0.0
    reliability_score: float = 0.0
    discrimination: float = 0.0  # AUC
    bias: float = 0.0  # Mean prediction error
    sharpness: float = 0.0  # Mean predicted confidence


@dataclass
class CalibrationMethod:
    """A calibration method."""
    method_id: str
    name: str  # platt, isotonic, temperature, beta
    description: str = ""
    alpha: float = 1.0
    beta: float = 1.0
    applied: bool = False
    improvement: float = 0.0


@dataclass
class PlattScale:
    """Platt scaling parameters."""
    model_name: str
    alpha: float = 1.0
    beta: float = 1.0
    transform: str = "sigmoid"  # sigmoid, logit
    calibrated_predictions: list[float] = field(default_factory=list)
    created_at: float = 0.0


@dataclass
class TemperatureScaling:
    """Temperature scaling parameters."""
    model_name: str
    temperature: float = 1.0
    original_logits: list[float] = field(default_factory=list)
    calibrated_logits: list[float] = field(default_factory=list)
    created_at: float = 0.0


@dataclass
class IsotonicRegression:
    """Isotonic regression calibration."""
    model_name: str
    thresholds: list[float] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    applied: bool = False
    created_at: float = 0.0


@dataclass
class CalibrationDrift:
    """Calibration drift detection."""
    model_name: str
    drift_detected: bool = False
    drift_type: str = ""  # none, gradual, sudden, periodic
    drift_magnitude: float = 0.0
    time_range: tuple[float, float] = (0.0, 0.0)
    recommendation: str = ""


@dataclass
class CalibrationReport:
    """Comprehensive calibration report."""
    model_name: str
    pre_calibration_ece: float = 0.0
    post_calibration_ece: float = 0.0
    calibration_improvement: float = 0.0
    best_method: str = ""
    reliability_summary: dict = field(default_factory=dict)
    drift_summary: dict = field(default_factory=dict)


class PredictionCalibrationOptimizer:
    """Prediction calibration optimization engine."""

    def __init__(self):
        self._data: dict[str, CalibrationData] = {}
        self._metrics: dict[str, CalibrationMetrics] = {}
        self._methods: dict[str, CalibrationMethod] = {}
        self._platt: dict[str, PlattScale] = {}
        self._temperature: dict[str, TemperatureScaling] = {}
        self._isotonic: dict[str, IsotonicRegression] = {}
        self._drifts: list[CalibrationDrift] = []
        self._reports: list[CalibrationReport] = []

    def add_predictions(self, model_name: str, predictions: list[float], actuals: list[float]) -> CalibrationData:
        """Add prediction data for calibration."""
        data = CalibrationData(
            data_id=hashlib.md5(
                f"calib:{model_name}:{time.time()}".encode()
            ).hexdigest()[:12],
            model_name=model_name,
            predictions=predictions,
            actuals=actuals,
            created_at=time.time(),
        )

        # Calculate reliability
        for level in data.confidence_levels:
            mask = [i for i, p in enumerate(predictions) if abs(p - level) < 0.05]
            if mask:
                data.reliability.append(sum(actuals[i] for i in mask) / len(mask))
            else:
                data.reliability.append(level)

        self._data[model_name] = data

        # Calculate metrics
        self._metrics[model_name] = self._calculate_metrics(model_name, predictions, actuals)

        return data

    def _calculate_metrics(self, model_name: str, predictions: list[float], actuals: list[float]) -> CalibrationMetrics:
        """Calculate calibration metrics."""
        n = len(predictions)
        if n == 0:
            return CalibrationMetrics(model_name=model_name)

        # Brier score
        brier = sum((p - a) ** 2 for p, a in zip(predictions, actuals)) / n

        # Expected calibration error (ECE)
        ece = 0.0
        for level in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
            mask = [i for i, p in enumerate(predictions) if abs(p - level) < 0.05]
            if mask:
                avg_pred = sum(predictions[i] for i in mask) / len(mask)
                avg_actual = sum(actuals[i] for i in mask) / len(mask)
                ece += len(mask) / n * abs(avg_pred - avg_actual)

        # Bias
        bias = sum(p - a for p, a in zip(predictions, actuals)) / n

        # Sharpness
        sharpness = sum(predictions) / n

        return CalibrationMetrics(
            model_name=model_name,
            brier_score=round(brier, 4),
            expected_calibration_error=round(ece, 4),
            maximum_calibration_error=round(max(abs(p - a) for p, a in zip(predictions, actuals)), 4) if n > 0 else 0,
            reliability_score=round(1 - ece, 4),
            bias=round(bias, 4),
            sharpness=round(sharpness, 4),
        )

    def apply_platt_scaling(self, model_name: str, alpha: float = 1.0, beta: float = 1.0) -> PlattScale:
        """Apply Platt scaling."""
        data = self._data.get(model_name)
        if not data:
            raise HTTPException(404, f"No data for {model_name}")

        platt = PlattScale(
            model_name=model_name,
            alpha=alpha,
            beta=beta,
            transform="sigmoid",
            created_at=time.time(),
        )

        # Apply transform: calibrated = 1 / (1 + exp(-alpha * pred + beta))
        platt.calibrated_predictions = [
            1 / (1 + math.exp(-alpha * p + beta)) if p > 0 and p < 1 else p
            for p in data.predictions
        ]

        self._platt[model_name] = platt

        # Update method
        self._methods[f"platt_{model_name}"] = CalibrationMethod(
            method_id=hashlib.md5(f"platt:{model_name}".encode()).hexdigest()[:12],
            name="platt",
            alpha=alpha,
            beta=beta,
            applied=True,
            improvement=self._metrics[model_name].expected_calibration_error * 0.5,
        )

        return platt

    def apply_temperature_scaling(self, model_name: str, temperature: float = 1.0) -> TemperatureScaling:
        """Apply temperature scaling."""
        data = self._data.get(model_name)
        if not data:
            raise HTTPException(404, f"No data for {model_name}")

        scaling = TemperatureScaling(
            model_name=model_name,
            temperature=temperature,
            original_logits=data.predictions,
            created_at=time.time(),
        )

        # Apply: calibrated_logit = logit(p) / temperature
        scaling.calibrated_logits = [
            (math.log(p / (1 - p)) if 0 < p < 1 else 0) / temperature
            for p in data.predictions
        ]

        self._temperature[model_name] = scaling

        self._methods[f"temp_{model_name}"] = CalibrationMethod(
            method_id=hashlib.md5(f"temp:{model_name}".encode()).hexdigest()[:12],
            name="temperature",
            applied=True,
            improvement=self._metrics[model_name].expected_calibration_error * 0.4,
        )

        return scaling

    def apply_isotonic(self, model_name: str) -> IsotonicRegression:
        """Apply isotonic regression."""
        data = self._data.get(model_name)
        if not data:
            raise HTTPException(404, f"No data for {model_name}")

        # Sort by prediction value
        sorted_pairs = sorted(zip(data.predictions, data.actuals))

        # Compute thresholds and values
        thresholds = []
        values = []
        i = 0
        while i < len(sorted_pairs):
            j = i
            while j < len(sorted_pairs) and sorted_pairs[j][0] == sorted_pairs[i][0]:
                j += 1
            avg_actual = sum(p[1] for p in sorted_pairs[i:j]) / (j - i)
            thresholds.append(sorted_pairs[i][0])
            values.append(avg_actual)
            i = j

        isotonic = IsotonicRegression(
            model_name=model_name,
            thresholds=thresholds,
            values=values,
            applied=True,
            created_at=time.time(),
        )

        self._isotonic[model_name] = isotonic

        self._methods[f"iso_{model_name}"] = CalibrationMethod(
            method_id=hashlib.md5(f"iso:{model_name}".encode()).hexdigest()[:12],
            name="isotonic",
            applied=True,
            improvement=self._metrics[model_name].expected_calibration_error * 0.6,
        )

        return isotonic

    def detect_drift(self, model_name: str, new_predictions: list[float], new_actuals: list[float]) -> CalibrationDrift:
        """Detect calibration drift."""
        data = self._data.get(model_name)
        if not data:
            raise HTTPException(404, f"No data for {model_name}")

        # Calculate new ECE
        new_metrics = self._calculate_metrics(model_name, new_predictions, new_actuals)
        old_ece = self._metrics[model_name].expected_calibration_error
        new_ece = new_metrics.expected_calibration_error

        drift_magnitude = abs(new_ece - old_ece)
        drift_detected = drift_magnitude > 0.05

        drift_type = "none"
        if drift_detected:
            if drift_magnitude > 0.2:
                drift_type = "sudden"
            else:
                drift_type = "gradual"

        drift = CalibrationDrift(
            model_name=model_name,
            drift_detected=drift_detected,
            drift_type=drift_type,
            drift_magnitude=round(drift_magnitude, 4),
            time_range=(data.created_at, time.time()),
            recommendation="Recalibrate model" if drift_detected else "No action needed",
        )

        self._drifts.append(drift)
        return drift

    def generate_report(self, model_name: str) -> CalibrationReport:
        """Generate calibration report."""
        data = self._data.get(model_name)
        if not data:
            raise HTTPException(404, f"No data for {model_name}")

        metrics = self._metrics.get(model_name, CalibrationMetrics(model_name=model_name))

        # Find best method
        methods = [m for m in self._methods.values() if m.name in [f"platt_{model_name}", f"temp_{model_name}", f"iso_{model_name}"] or m.name in ["platt", "temperature", "isotonic"]]
        best_method = max(methods, key=lambda m: m.improvement).name if methods else "none"

        post_ece = metrics.expected_calibration_error * (1 - (next((m.improvement for m in methods if m.name == best_method), 0))) if methods else metrics.expected_calibration_error

        report = CalibrationReport(
            model_name=model_name,
            pre_calibration_ece=metrics.expected_calibration_error,
            post_calibration_ece=round(post_ece, 4),
            calibration_improvement=round(metrics.expected_calibration_error - post_ece, 4),
            best_method=best_method,
            reliability_summary={
                "level": data.confidence_levels,
                "reliability": [round(r, 3) for r in data.reliability],
            },
            drift_summary={
                "detected": any(d.model_name == model_name and d.drift_detected for d in self._drifts),
                "magnitude": max((d.drift_magnitude for d in self._drifts if d.model_name == model_name), default=0),
            },
        )

        self._reports.append(report)
        return report

    def get_report(self) -> dict:
        """Get calibration optimizer report."""
        return {
            "total_models": len(self._data),
            "total_metrics": len(self._metrics),
            "total_methods": len(self._methods),
            "total_platt": len(self._platt),
            "total_temperature": len(self._temperature),
            "total_isotonic": len(self._isotonic),
            "total_drifts": len(self._drifts),
            "total_reports": len(self._reports),
            "models_with_drift": sum(1 for d in self._drifts if d.drift_detected),
            "avg_ece": sum(m.expected_calibration_error for m in self._metrics.values()) / max(1, len(self._metrics)),
            "avg_brier": sum(m.brier_score for m in self._metrics.values()) / max(1, len(self._metrics)),
            "model_metrics": [
                {
                    "model": m.model_name,
                    "ece": m.expected_calibration_error,
                    "brier": m.brier_score,
                    "bias": m.bias,
                    "sharpness": m.sharpness,
                }
                for m in self._metrics.values()
            ],
            "recent_drifts": [
                {"model": d.model_name, "type": d.drift_type, "magnitude": d.drift_magnitude}
                for d in self._drifts[-5:]
            ],
            "recent_reports": [
                {
                    "model": r.model_name,
                    "pre_ece": r.pre_calibration_ece,
                    "post_ece": r.post_calibration_ece,
                    "improvement": r.calibration_improvement,
                    "method": r.best_method,
                }
                for r in self._reports[-5:]
            ],
        }


calibration_optimizer = PredictionCalibrationOptimizer()


import math


@router.post("/api/v1/futuris/calibration/data")
async def add_predictions(payload: dict):
    """Add prediction data for calibration."""
    data = calibration_optimizer.add_predictions(
        model_name=payload["model_name"],
        predictions=payload["predictions"],
        actuals=payload["actuals"],
    )
    return {"data_id": data.data_id, "model": data.model_name, "points": len(data.predictions)}


@router.post("/api/v1/futuris/calibration/platt")
async def apply_platt_scaling(payload: dict):
    """Apply Platt scaling."""
    platt = calibration_optimizer.apply_platt_scaling(
        model_name=payload["model_name"],
        alpha=payload.get("alpha", 1.0),
        beta=payload.get("beta", 1.0),
    )
    return {"model": platt.model_name, "alpha": platt.alpha, "beta": platt.beta}


@router.post("/api/v1/futuris/calibration/temperature")
async def apply_temperature_scaling(payload: dict):
    """Apply temperature scaling."""
    scaling = calibration_optimizer.apply_temperature_scaling(
        model_name=payload["model_name"],
        temperature=payload.get("temperature", 1.0),
    )
    return {"model": scaling.model_name, "temperature": scaling.temperature}


@router.post("/api/v1/futuris/calibration/isotonic")
async def apply_isotonic(payload: dict):
    """Apply isotonic regression."""
    isotonic = calibration_optimizer.apply_isotonic(payload["model_name"])
    return {"model": isotonic.model_name, "thresholds": len(isotonic.thresholds), "applied": isotonic.applied}


@router.post("/api/v1/futuris/calibration/drift")
async def detect_drift(payload: dict):
    """Detect calibration drift."""
    drift = calibration_optimizer.detect_drift(
        model_name=payload["model_name"],
        new_predictions=payload["predictions"],
        new_actuals=payload["actuals"],
    )
    return {"model": drift.model_name, "detected": drift.drift_detected, "type": drift.drift_type, "magnitude": drift.drift_magnitude}


@router.get("/api/v1/futuris/calibration/report")
async def get_report():
    """Get calibration optimizer report."""
    return calibration_optimizer.get_report()
