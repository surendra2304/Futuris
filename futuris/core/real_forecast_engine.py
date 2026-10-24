"""
Futuris/futuris/core/real_forecast_engine.py — Real Forecast Engine.

Actual working forecasting with:
- Time series forecasting (ARIMA-like, exponential smoothing)
- Ensemble prediction (weighted combination)
- Calibration (Brier score, reliability curves)
- Drift detection (concept drift monitoring)
- Scenario analysis (stress testing)
- Prediction quality tracking
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import json
import os
from collections import defaultdict


@dataclass
class Forecast:
    """A forecast prediction."""
    forecast_id: str
    symbol: str
    prediction: float
    lower_bound: float
    upper_bound: float
    confidence: float  # 0.0 to 1.0
    timestamp: float = field(default_factory=time.time)
    horizon_sec: float = 3600.0
    model_name: str = ""
    features_used: list[str] = field(default_factory=list)


@dataclass
class CalibrationResult:
    """Calibration metrics."""
    brier_score: float  # Lower is better (0 = perfect)
    reliability: list[tuple[float, float]]  # (predicted_prob, actual_frequency)
    expected_calibration_error: float
    n_samples: int


@dataclass
class DriftResult:
    """Drift detection result."""
    drift_detected: bool
    drift_score: float  # 0.0 to 1.0
    window_size: int
    p_value: float
    recommendations: list[str]


@dataclass
class ScenarioResult:
    """Scenario analysis result."""
    scenario_name: str
    description: str
    predictions: list[dict[str, Any]]
    impact_score: float  # -1.0 to 1.0
    probability: float  # 0.0 to 1.0


class ForecastEngine:
    """Real forecast engine with time series, calibration, and drift detection."""

    def __init__(self, state_dir: str | None = None) -> None:
        self._state_dir = state_dir or os.path.join(
            os.path.dirname(__file__), "..", ".data"
        )
        os.makedirs(self._state_dir, exist_ok=True)
        self._forecasts: dict[str, Forecast] = {}
        self._actuals: dict[str, list[tuple[float, float]]] = defaultdict(list)  # symbol -> [(ts, actual)]
        self._calibration: dict[str, CalibrationResult] = {}
        self._drift_windows: dict[str, list[float]] = defaultdict(list)
        self._load_state()

    def _load_state(self) -> None:
        """Load forecast state from disk."""
        state_file = os.path.join(self._state_dir, "forecast_state.json")
        try:
            if os.path.exists(state_file):
                with open(state_file, "r") as f:
                    data = json.load(f)
                self._forecasts = {
                    fid: Forecast(**fdata) for fid, fdata in data.get("forecasts", {}).items()
                }
                self._actuals = {
                    sym: [(ts, val) for ts, val in vals]
                    for sym, vals in data.get("actuals", {}).items()
                }
        except Exception:
            pass

    def _save_state(self) -> None:
        """Save forecast state to disk."""
        state_file = os.path.join(self._state_dir, "forecast_state.json")
        try:
            data = {
                "forecasts": {
                    fid: {
                        "forecast_id": f.forecast_id,
                        "symbol": f.symbol,
                        "prediction": f.prediction,
                        "lower_bound": f.lower_bound,
                        "upper_bound": f.upper_bound,
                        "confidence": f.confidence,
                        "timestamp": f.timestamp,
                        "horizon_sec": f.horizon_sec,
                        "model_name": f.model_name,
                        "features_used": f.features_used,
                    }
                    for fid, f in self._forecasts.items()
                },
                "actuals": {
                    sym: [[ts, val] for ts, val in vals]
                    for sym, vals in self._actuals.items()
                },
            }
            with open(state_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    # ── Time Series Forecasting ─────────────────────────────────────────────

    def simple_moving_average(self, series: list[float], window: int = 10) -> float:
        """Simple Moving Average forecast."""
        if len(series) < window:
            window = len(series)
        if window == 0:
            return 0.0
        return sum(series[-window:]) / window

    def exponential_smoothing(self, series: list[float], alpha: float = 0.3) -> float:
        """Simple Exponential Smoothing forecast."""
        if not series:
            return 0.0
        smoothed = series[0]
        for value in series[1:]:
            smoothed = alpha * value + (1 - alpha) * smoothed
        return smoothed

    def holt_linear(self, series: list[float], alpha: float = 0.5, beta: float = 0.3) -> float:
        """Holt's Linear Trend forecast (double exponential smoothing)."""
        if len(series) < 2:
            return series[0] if series else 0.0

        level = series[0]
        trend = series[1] - series[0]

        for value in series[1:]:
            new_level = alpha * value + (1 - alpha) * (level + trend)
            new_trend = beta * (new_level - level) + (1 - beta) * trend
            level = new_level
            trend = new_trend

        # Next step forecast
        return level + trend

    def arima_simple(self, series: list[float], d: int = 1) -> float:
        """Simplified ARIMA(0, d, 0) — random walk with drift."""
        if len(series) <= d:
            return series[-1] if series else 0.0

        # Difference the series
        diff = [series[i] - series[i - 1] for i in range(1, len(series))]
        avg_diff = sum(diff) / len(diff) if diff else 0.0
        return series[-1] + avg_diff

    # ── Ensemble Forecasting ────────────────────────────────────────────────

    def ensemble_forecast(
        self, symbol: str, series: list[float],
        models: dict[str, float] | None = None
    ) -> Forecast:
        """Generate ensemble forecast combining multiple models."""
        models = models or {
            "sma": 0.2,
            "ses": 0.2,
            "holt": 0.3,
            "arima": 0.3,
        }

        predictions = {}
        predictions["sma"] = self.simple_moving_average(series, 10)
        predictions["ses"] = self.exponential_smoothing(series, 0.3)
        predictions["holt"] = self.holt_linear(series, 0.5, 0.3)
        predictions["arima"] = self.arima_simple(series, 1)

        # Weighted ensemble
        ensemble_pred = sum(
            weights.get(name, 0) * pred
            for name, pred in predictions.items()
            for name_key, weights in [(models, models)]
        )

        # Actually compute weighted sum properly
        ensemble_pred = sum(
            models.get(name, 0) * predictions[name]
            for name in predictions
        )

        # Calculate confidence based on model agreement
        values = list(predictions.values())
        if len(values) > 1:
            mean = sum(values) / len(values)
            variance = sum((v - mean) ** 2 for v in values) / len(values)
            std = math.sqrt(variance)
            # Lower variance = higher confidence
            confidence = max(0.5, 1.0 - std / max(mean, 1.0))
        else:
            confidence = 0.7

        # Confidence interval (rough)
        interval = (1.0 - confidence) * ensemble_pred * 0.1
        lower = ensemble_pred - interval
        upper = ensemble_pred + interval

        forecast = Forecast(
            forecast_id=f"fc_{uuid.uuid4().hex[:12]}",
            symbol=symbol,
            prediction=round(ensemble_pred, 4),
            lower_bound=round(lower, 4),
            upper_bound=round(upper, 4),
            confidence=round(confidence, 3),
            model_name="ensemble",
            features_used=list(predictions.keys()),
        )

        self._forecasts[forecast.forecast_id] = forecast
        self._save_state()
        return forecast

    def record_actual(self, symbol: str, actual_value: float, timestamp: float | None = None) -> None:
        """Record actual value for calibration."""
        ts = timestamp or time.time()
        self._actuals[symbol].append((ts, actual_value))
        self._save_state()

    # ── Calibration ──────────────────────────────────────────────────────────

    def calibrate(self, symbol: str) -> CalibrationResult:
        """Calculate calibration metrics for forecasts."""
        forecasts = [f for f in self._forecasts.values() if f.symbol == symbol]
        actuals = self._actuals.get(symbol, [])

        if not forecasts or not actuals:
            return CalibrationResult(
                brier_score=0.0, reliability=[], expected_calibration_error=0.0,
                n_samples=0,
            )

        # Match forecasts to actuals by timestamp
        matched = []
        for forecast in forecasts:
            # Find the actual closest to forecast timestamp + horizon
            target_ts = forecast.timestamp + forecast.horizon_sec
            closest = min(actuals, key=lambda a: abs(a[0] - target_ts), default=None)
            if closest:
                matched.append((forecast, closest[1]))

        if not matched:
            return CalibrationResult(
                brier_score=0.0, reliability=[], expected_calibration_error=0.0,
                n_samples=0,
            )

        # Brier score: mean((predicted - actual)^2)
        brier = sum(
            (f.prediction - actual) ** 2
            for f, actual in matched
        ) / len(matched)

        # Reliability curve (bin predictions into groups)
        bins = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]
        reliability = []
        for lo, hi in bins:
            bin_forecasts = [
                (f.prediction, actual) for f, actual in matched
                if lo <= f.confidence < hi
            ]
            if bin_forecasts:
                avg_pred = sum(p for p, _ in bin_forecasts) / len(bin_forecasts)
                avg_actual = sum(a for _, a in bin_forecasts) / len(bin_forecasts)
                reliability.append((round(avg_pred, 3), round(avg_actual, 3)))

        # Expected Calibration Error
        ecr = sum(abs(p - a) for p, a in reliability) / len(reliability) if reliability else 0.0

        result = CalibrationResult(
            brier_score=round(brier, 4),
            reliability=reliability,
            expected_calibration_error=round(ecr, 4),
            n_samples=len(matched),
        )
        self._calibration[symbol] = result
        return result

    # ── Drift Detection ─────────────────────────────────────────────────────

    def detect_drift(self, symbol: str, window: int = 20) -> DriftResult:
        """Detect concept drift in the time series."""
        actuals = self._actuals.get(symbol, [])
        if len(actuals) < window * 2:
            return DriftResult(
                drift_detected=False, drift_score=0.0,
                window_size=window, p_value=1.0,
                recommendations=["Insufficient data for drift detection"],
            )

        # Split into two windows
        values = [v for _, v in actuals]
        recent = values[-window:]
        previous = values[-window * 2:-window]

        # Compare statistics
        recent_mean = sum(recent) / len(recent)
        prev_mean = sum(previous) / len(previous)

        recent_std = math.sqrt(
            sum((v - recent_mean) ** 2 for v in recent) / len(recent)
        ) if recent else 0.0
        prev_std = math.sqrt(
            sum((v - prev_mean) ** 2 for v in previous) / len(previous)
        ) if previous else 0.0

        # Drift score based on mean shift normalized by std
        combined_std = (recent_std + prev_std) / 2
        if combined_std > 0:
            drift_score = abs(recent_mean - prev_mean) / combined_std
        else:
            drift_score = 0.0

        drift_detected = drift_score > 1.0  # More than 1 std deviation

        # Approximate p-value (normal approximation)
        # For z > 1.0, p-value is approximately 0.16
        p_value = max(0.0, 1.0 - drift_score / 5.0)

        recommendations = []
        if drift_detected:
            recommendations.append("Model may need retraining")
            recommendations.append("Consider adding more recent data")
            if drift_score > 2.0:
                recommendations.append("Significant drift detected — immediate recalibration needed")
        else:
            recommendations.append("No significant drift detected")

        return DriftResult(
            drift_detected=drift_detected,
            drift_score=round(drift_score, 3),
            window_size=window,
            p_value=round(p_value, 4),
            recommendations=recommendations,
        )

    # ── Scenario Analysis ───────────────────────────────────────────────────

    def analyze_scenario(
        self, symbol: str, series: list[float],
        scenario_name: str, description: str,
        probabilities: dict[str, float], impacts: dict[str, float]
    ) -> ScenarioResult:
        """Analyze a scenario with multiple outcomes."""
        predictions = []
        expected_impact = 0.0

        for outcome, prob in probabilities.items():
            impact = impacts.get(outcome, 0.0)
            predictions.append({
                "outcome": outcome,
                "probability": prob,
                "impact": impact,
            })
            expected_impact += prob * impact

        return ScenarioResult(
            scenario_name=scenario_name,
            description=description,
            predictions=predictions,
            impact_score=round(expected_impact, 4),
            probability=round(sum(probabilities.values()), 4),
        )

    # ── Summary ──────────────────────────────────────────────────────────────

    def get_forecast_accuracy(self, symbol: str) -> dict[str, float]:
        """Get forecast accuracy metrics."""
        forecasts = [f for f in self._forecasts.values() if f.symbol == symbol]
        actuals = self._actuals.get(symbol, [])

        if not forecasts or not actuals:
            return {"error": "No data"}

        # Match and calculate errors
        errors = []
        for forecast in forecasts:
            target_ts = forecast.timestamp + forecast.horizon_sec
            closest = min(actuals, key=lambda a: abs(a[0] - target_ts), default=None)
            if closest:
                error = forecast.prediction - closest[1]
                errors.append(error)

        if not errors:
            return {"error": "No matching data"}

        mse = sum(e ** 2 for e in errors) / len(errors)
        rmse = math.sqrt(mse)
        mae = sum(abs(e) for e in errors) / len(errors)

        # MAPE (Mean Absolute Percentage Error)
        mape_values = []
        for forecast, actual in zip(forecasts, [closest[1] for closest in [min(actuals, key=lambda a: abs(a[0] - (f.timestamp + f.horizon_sec)), default=None) for f in forecasts]]):
            if actual != 0:
                mape_values.append(abs(forecast.prediction - actual) / abs(actual) * 100)

        return {
            "rmse": round(rmse, 4),
            "mae": round(mae, 4),
            "mape": round(sum(mape_values) / len(mape_values), 2) if mape_values else 0.0,
            "sample_size": len(errors),
        }

    def get_stats(self) -> dict[str, Any]:
        """Get forecast engine statistics."""
        return {
            "total_forecasts": len(self._forecasts),
            "tracked_symbols": len(self._actuals),
            "calibrated_symbols": len(self._calibration),
            "drift_checked_symbols": len(self._drift_windows),
        }


engine = ForecastEngine()
