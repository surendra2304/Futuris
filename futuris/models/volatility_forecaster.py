"""Quantitative GARCH(1,1) dynamic volatility clustering and heteroskedasticity forecaster."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from futuris.models.base import (
    ModelPrediction,
    PredictionIntervals,
    calculate_exceedance_probability,
    compute_config_hash,
)


class GARCHVolatilityModelAdapter:
    """GARCH(1,1) model for time-varying conditional variance and volatility regime clustering.

    Variance equation:
        sigma_t^2 = omega + alpha * epsilon_{t-1}^2 + beta * sigma_{t-1}^2
    Subject to:
        omega > 0, alpha >= 0, beta >= 0, alpha + beta < 1 (covariance stationarity).
    """

    def __init__(
        self,
        name: str = "garch_volatility",
        config: dict[str, Any] | None = None,
    ) -> None:
        self._name = name
        self._config = config or {}
        self.omega: float = 0.05
        self.alpha: float = 0.10
        self.beta: float = 0.85
        self.mu: float = 0.0
        self.fitted: bool = False
        self.residuals: np.ndarray = np.array([])
        self.conditional_variances: np.ndarray = np.array([])
        self.as_of: datetime | None = None
        self.y_history: np.ndarray | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    @property
    def persistence(self) -> float:
        """Total persistence of volatility shocks: alpha + beta."""
        return self.alpha + self.beta

    @property
    def unconditional_variance(self) -> float:
        """Long-run unconditional variance omega / (1 - alpha - beta)."""
        denom = max(1e-5, 1.0 - self.persistence)
        return self.omega / denom

    @property
    def half_life_steps(self) -> float:
        """Half-life of a volatility shock in steps: -ln(2) / ln(alpha + beta)."""
        persist = min(0.999, max(0.01, self.persistence))
        return -math.log(2.0) / math.log(persist)

    def get_config_hash(self) -> str:
        return compute_config_hash(self._config)

    def fit(self, x: pd.DataFrame, y: pd.Series, as_of: datetime) -> GARCHVolatilityModelAdapter:
        """Fit GARCH(1,1) parameters via constrained Maximum Likelihood Estimation (MLE)."""
        _ = x
        self.as_of = as_of
        vals = y.to_numpy(dtype=float)
        self.y_history = vals
        n = len(vals)

        if n < 8:
            # Insufficient sample for robust MLE; use variance targeting defaults
            sample_var = float(np.var(vals)) if n > 1 else 1.0
            self.mu = float(np.mean(vals)) if n > 0 else 0.0
            self.alpha = 0.10
            self.beta = 0.80
            self.omega = float(max(1e-4, sample_var * (1.0 - self.alpha - self.beta)))
            self.conditional_variances = np.full(max(1, n), sample_var)
            self.residuals = vals - self.mu
            self.fitted = True
            return self

        # Differences / returns for mean estimation
        self.mu = float(np.mean(vals))
        eps = vals - self.mu
        sample_var = float(np.var(eps))
        if sample_var <= 1e-6:
            sample_var = 1.0

        # Negative Gaussian log-likelihood objective
        def _neg_log_likelihood(params: np.ndarray) -> float:
            omega, alpha, beta = params
            if omega <= 0 or alpha < 0 or beta < 0 or (alpha + beta) >= 0.999:
                return 1e10

            variances = np.zeros(n)
            variances[0] = sample_var
            ll = 0.0
            for t in range(1, n):
                variances[t] = omega + alpha * (eps[t - 1] ** 2) + beta * variances[t - 1]
                v_t = max(1e-6, variances[t])
                ll += math.log(v_t) + (eps[t] ** 2) / v_t

            return 0.5 * ll

        # Initial parameter guess
        init_alpha = 0.08
        init_beta = 0.85
        init_omega = sample_var * (1.0 - init_alpha - init_beta)
        p0 = np.array([max(1e-4, init_omega), init_alpha, init_beta])

        bounds = [
            (1e-5, sample_var * 2.0),
            (0.01, 0.40),
            (0.40, 0.95),
        ]

        def _stationarity_constraint(params: np.ndarray) -> float:
            # 1.0 - (alpha + beta) >= 0.001
            return 0.999 - (params[1] + params[2])

        constraints = [{"type": "ineq", "fun": _stationarity_constraint}]

        try:
            res = minimize(
                _neg_log_likelihood,
                p0,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
                options={"maxiter": 200, "ftol": 1e-6},
            )
            if res.success and not np.isnan(res.x).any():
                self.omega = float(res.x[0])
                self.alpha = float(res.x[1])
                self.beta = float(res.x[2])
            else:
                self.omega = float(max(1e-4, init_omega))
                self.alpha = init_alpha
                self.beta = init_beta
        except Exception:
            self.omega = float(max(1e-4, init_omega))
            self.alpha = init_alpha
            self.beta = init_beta

        # Filter fitted conditional variances across history
        variances = np.zeros(n)
        variances[0] = sample_var
        for t in range(1, n):
            variances[t] = (
                self.omega + self.alpha * (eps[t - 1] ** 2) + self.beta * variances[t - 1]
            )

        self.conditional_variances = variances
        self.residuals = eps
        self.fitted = True
        return self

    def predict(
        self,
        horizon: int,
        capacity_threshold: float | None = None,
        probability_method: str = "empirical",
    ) -> ModelPrediction:
        """Forecast future conditional variance, uncertainty intervals, and regime shifts."""
        if not self.fitted or self.y_history is None:
            msg = "Model must be fitted before predict() is called."
            raise ValueError(msg)

        last_val = float(self.y_history[-1])
        last_var = float(self.conditional_variances[-1])
        last_eps = float(self.residuals[-1])

        # Step 1 conditional variance
        sigma2_next = self.omega + self.alpha * (last_eps**2) + self.beta * last_var
        unc_var = self.unconditional_variance
        persistence = self.persistence

        forecast_variances = np.zeros(horizon)
        forecast_points = np.zeros(horizon)

        for h in range(horizon):
            if h == 0:
                forecast_variances[h] = sigma2_next
            else:
                # Analytic multi-step GARCH(1,1) variance projection
                decay = persistence ** h
                forecast_variances[h] = unc_var + decay * (sigma2_next - unc_var)
            # Conditional mean projection: smooth mean reversion
            forecast_points[h] = last_val + (self.mu - last_val) * (1.0 - math.exp(-0.1 * (h + 1)))

        z_90 = 1.645
        intervals: list[PredictionIntervals] = []
        for h in range(horizon):
            cond_std = math.sqrt(max(1e-6, forecast_variances[h]))
            pt = forecast_points[h]
            lower = float(pt - z_90 * cond_std)
            upper = float(pt + z_90 * cond_std)
            intervals.append(
                PredictionIntervals(
                    lower=round(lower, 2),
                    central=round(float(pt), 2),
                    upper=round(upper, 2),
                )
            )

        prob: float | None = None
        if capacity_threshold is not None:
            prob = calculate_exceedance_probability(
                point_forecast=forecast_points,
                residuals=self.residuals,
                capacity_threshold=capacity_threshold,
                method=probability_method,
            )

        # Regimes: check current volatility relative to unconditional variance
        ratio = last_var / max(1e-5, unc_var)
        if ratio > 1.5:
            regime = "high_volatility"
        elif ratio < 0.7:
            regime = "low_volatility"
        else:
            regime = "normal_volatility"

        metadata = {
            "model": self.name,
            "config_hash": self.get_config_hash(),
            "omega": round(self.omega, 6),
            "alpha_arch": round(self.alpha, 4),
            "beta_garch": round(self.beta, 4),
            "persistence": round(self.persistence, 4),
            "unconditional_variance": round(unc_var, 4),
            "half_life_steps": round(self.half_life_steps, 2),
            "current_conditional_volatility": round(math.sqrt(max(1e-6, last_var)), 4),
            "volatility_regime": regime,
        }

        return ModelPrediction(
            point_forecast=[round(float(v), 2) for v in forecast_points],
            intervals=intervals,
            range_lower=round(min(p.lower for p in intervals), 2),
            range_upper=round(max(p.upper for p in intervals), 2),
            central_estimate=round(float(np.mean(forecast_points)), 2),
            exceedance_probability=prob,
            metadata=metadata,
        )
