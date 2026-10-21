"""Viral diffusion and cascade intensity forecasting adapters (Bass & Hawkes processes)."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

from futuris.models.base import (
    ModelPrediction,
    PredictionIntervals,
    calculate_exceedance_probability,
    compute_config_hash,
)


def _bass_cumulative(t: np.ndarray, m: float, p: float, q: float) -> np.ndarray:
    """Cumulative adopters F(t) under the Bass diffusion model."""
    exp_term = np.exp(-(p + q) * t)
    denom = 1.0 + (q / max(p, 1e-6)) * exp_term
    denom = np.where(np.abs(denom) < 1e-6, 1e-6, denom)
    return m * (1.0 - exp_term) / denom


def _bass_rate(t: np.ndarray, m: float, p: float, q: float) -> np.ndarray:
    """Instantaneous adoption/infection rate S(t) under the Bass diffusion model."""
    exp_term = np.exp(-(p + q) * t)
    num = (p + q) ** 2 * exp_term
    denom = (1.0 + (q / max(p, 1e-6)) * exp_term) ** 2
    denom = np.where(np.abs(denom) < 1e-6, 1e-6, denom)
    return m * (num / max(p, 1e-6)) / denom


class BassDiffusionModelAdapter:
    """Bass diffusion model forecasting innovation (p) vs social imitation/virality (q)."""

    def __init__(
        self,
        name: str = "bass_diffusion",
        config: dict[str, Any] | None = None,
    ) -> None:
        self._name = name
        self._config = config or {}
        self.p: float = 0.03  # Innovation coefficient (typical ~ 0.01 - 0.05)
        self.q: float = 0.38  # Imitation coefficient (typical ~ 0.30 - 0.50)
        self.m: float = 10000.0  # Total market/population capacity
        self.fitted: bool = False
        self.residuals: np.ndarray = np.array([])
        self.as_of: datetime | None = None
        self.y_history: np.ndarray | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    def get_config_hash(self) -> str:
        return compute_config_hash(self._config)

    def fit(self, x: pd.DataFrame, y: pd.Series, as_of: datetime) -> BassDiffusionModelAdapter:
        """Fit Bass parameters (m, p, q) to empirical time-series observations."""
        _ = x
        self.as_of = as_of
        y_vals = np.maximum(0.0, y.to_numpy(dtype=float))
        self.y_history = y_vals
        n = len(y_vals)

        if n < 4:
            # Insufficient points for non-linear fit: heuristic initialization
            self.m = float(max(np.sum(y_vals) * 2.0, 100.0))
            self.p = 0.03
            self.q = 0.35
            self.fitted = True
            self.residuals = np.zeros(n)
            return self

        t_steps = np.arange(1, n + 1, dtype=float)
        y_cum = np.cumsum(y_vals)

        # Bounds: m > 0, 0 < p < 0.20, 0 < q < 1.20
        p0 = [float(y_cum[-1] * 1.5), 0.03, 0.35]
        bounds = ([float(y_cum[-1]), 0.0001, 0.001], [float(y_cum[-1] * 20.0), 0.25, 1.5])

        try:
            popt, _ = curve_fit(
                _bass_cumulative,
                t_steps,
                y_cum,
                p0=p0,
                bounds=bounds,
                maxfev=1500,
            )
            self.m, self.p, self.q = float(popt[0]), float(popt[1]), float(popt[2])
        except Exception:
            # Fallback to sensible estimation from empirical peak
            peak_idx = int(np.argmax(y_vals))
            self.m = float(y_cum[-1] * 1.8)
            self.q = float(min(0.6, max(0.2, (peak_idx / max(1, n)))))
            self.p = float(max(0.01, self.q / 10.0))

        fitted_cum = _bass_cumulative(t_steps, self.m, self.p, self.q)
        fitted_rates = np.diff(np.insert(fitted_cum, 0, 0.0))
        self.residuals = y_vals - fitted_rates
        self.fitted = True
        return self

    def predict(
        self,
        horizon: int,
        capacity_threshold: float | None = None,
        probability_method: str = "empirical",
    ) -> ModelPrediction:
        """Forecast future viral adoption steps, peak timing, and uncertainty bands."""
        if not self.fitted or self.y_history is None:
            msg = "Model must be fitted before predict() is called."
            raise ValueError(msg)

        n = len(self.y_history)
        future_t = np.arange(n + 1, n + horizon + 1, dtype=float)

        # Predict cumulative adopters and take first difference for rate
        t_all = np.insert(future_t, 0, float(n))
        cum_all = _bass_cumulative(t_all, self.m, self.p, self.q)
        future_rates = np.diff(cum_all)
        future_rates = np.maximum(0.0, future_rates)

        # Standard error of residuals
        std_res = float(np.std(self.residuals)) if len(self.residuals) > 0 else 1.0
        z_90 = 1.645

        intervals: list[PredictionIntervals] = []
        for i, rate in enumerate(future_rates):
            step_scale = math.sqrt(i + 1)
            lower = max(0.0, float(rate - z_90 * std_res * step_scale))
            upper = float(rate + z_90 * std_res * step_scale)
            intervals.append(
                PredictionIntervals(
                    lower=round(lower, 2),
                    central=round(float(rate), 2),
                    upper=round(upper, 2),
                )
            )

        range_lower = min(p.lower for p in intervals) if intervals else 0.0
        range_upper = max(p.upper for p in intervals) if intervals else 0.0
        central = float(np.mean(future_rates)) if len(future_rates) > 0 else 0.0

        # Calculate peak step
        peak_t = math.log(max(self.q, 1e-4) / max(self.p, 1e-4)) / (self.p + self.q)
        peak_rate = self.m * ((self.p + self.q) ** 2) / (4.0 * max(self.q, 1e-4))

        prob: float | None = None
        if capacity_threshold is not None:
            prob = calculate_exceedance_probability(
                point_forecast=future_rates,
                residuals=self.residuals,
                capacity_threshold=capacity_threshold,
                method=probability_method,
            )

        metadata = {
            "model": self.name,
            "config_hash": self.get_config_hash(),
            "p_innovation": round(self.p, 5),
            "q_imitation_virality": round(self.q, 5),
            "market_potential_M": round(self.m, 1),
            "theoretical_peak_step": round(peak_t, 2),
            "theoretical_peak_rate": round(peak_rate, 2),
        }

        return ModelPrediction(
            point_forecast=[round(float(v), 2) for v in future_rates],
            intervals=intervals,
            range_lower=round(range_lower, 2),
            range_upper=round(range_upper, 2),
            central_estimate=round(central, 2),
            exceedance_probability=prob,
            metadata=metadata,
        )


class HawkesProcessCascadeAdapter:
    """Self-exciting point process model for viral cascades and information diffusion."""

    def __init__(
        self,
        name: str = "hawkes_cascade",
        baseline_rate: float = 2.0,
        decay_beta: float = 0.5,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._name = name
        self._config = config or {}
        self.mu: float = baseline_rate  # Baseline exogenous arrival intensity
        self.alpha: float = 0.35  # Self-excitation magnitude per event
        self.beta: float = decay_beta  # Exponential relaxation decay rate
        self.fitted: bool = False
        self.residuals: np.ndarray = np.array([])
        self.y_history: np.ndarray | None = None
        self.as_of: datetime | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def branching_ratio(self) -> float:
        """Reproductive ratio eta = alpha / beta (<1: subcritical; >=1: viral cascade)."""
        return self.alpha / max(self.beta, 1e-4)

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    def get_config_hash(self) -> str:
        return compute_config_hash(self._config)

    def fit(self, x: pd.DataFrame, y: pd.Series, as_of: datetime) -> HawkesProcessCascadeAdapter:
        _ = x
        self.as_of = as_of
        vals = np.maximum(0.0, y.to_numpy(dtype=float))
        self.y_history = vals
        n = len(vals)

        if n > 2:
            # Estimate mean and auto-correlation lag-1
            mean_val = float(np.mean(vals))
            diffs = vals - mean_val
            c0 = float(np.dot(diffs, diffs) / n)
            c1 = float(np.dot(diffs[:-1], diffs[1:]) / max(1, n - 1)) if c0 > 0 else 0.0
            r1 = max(0.01, min(0.95, c1 / max(c0, 1e-6)))

            self.beta = max(0.1, -math.log(r1))
            self.alpha = min(self.beta * 0.95, self.beta * (r1 * 0.9))
            self.mu = max(0.1, mean_val * (1.0 - self.branching_ratio))
        else:
            self.mu = float(np.mean(vals)) if n > 0 else 2.0
            self.alpha = 0.2
            self.beta = 0.5

        # Compute fitted intensities and residuals
        fitted_intensity = np.zeros(n)
        for i in range(n):
            if i == 0:
                fitted_intensity[i] = self.mu
            else:
                lag_contrib = float(np.sum(self.alpha * np.exp(-self.beta * np.arange(i, 0, -1))))
                fitted_intensity[i] = self.mu + lag_contrib
        self.residuals = vals - fitted_intensity
        self.fitted = True
        return self

    def predict(
        self,
        horizon: int,
        capacity_threshold: float | None = None,
        probability_method: str = "empirical",
    ) -> ModelPrediction:
        if not self.fitted or self.y_history is None:
            msg = "Model must be fitted before predict() is called."
            raise ValueError(msg)

        eta = self.branching_ratio
        asymptotic_mean = self.mu / max(1e-4, (1.0 - min(0.98, eta)))
        last_val = float(self.y_history[-1]) if len(self.y_history) > 0 else self.mu

        forecast_rates = np.zeros(horizon)
        for h in range(horizon):
            decay = math.exp(-self.beta * (h + 1))
            forecast_rates[h] = asymptotic_mean + (last_val - asymptotic_mean) * decay

        std_res = float(np.std(self.residuals)) if len(self.residuals) > 0 else 1.0
        z_90 = 1.645

        intervals: list[PredictionIntervals] = []
        for i, val in enumerate(forecast_rates):
            step_scale = math.sqrt(i + 1)
            lower = max(0.0, float(val - z_90 * std_res * step_scale))
            upper = float(val + z_90 * std_res * step_scale)
            intervals.append(
                PredictionIntervals(
                    lower=round(lower, 2),
                    central=round(float(val), 2),
                    upper=round(upper, 2),
                )
            )

        prob: float | None = None
        if capacity_threshold is not None:
            prob = calculate_exceedance_probability(
                point_forecast=forecast_rates,
                residuals=self.residuals,
                capacity_threshold=capacity_threshold,
                method=probability_method,
            )

        metadata = {
            "model": self.name,
            "config_hash": self.get_config_hash(),
            "branching_ratio": round(eta, 4),
            "cascade_regime": (
                "supercritical" if eta >= 1.0 else "subcritical_stable"
            ),
            "baseline_mu": round(self.mu, 4),
            "excitation_alpha": round(self.alpha, 4),
            "relaxation_beta": round(self.beta, 4),
        }

        return ModelPrediction(
            point_forecast=[round(float(v), 2) for v in forecast_rates],
            intervals=intervals,
            range_lower=round(min(p.lower for p in intervals), 2),
            range_upper=round(max(p.upper for p in intervals), 2),
            central_estimate=round(float(np.mean(forecast_rates)), 2),
            exceedance_probability=prob,
            metadata=metadata,
        )
