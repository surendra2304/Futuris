"""ModelAdapter protocol, prediction dataclasses, and uncertainty estimation."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

import numpy as np
import pandas as pd
from scipy.stats import norm


@dataclass(frozen=True)
class PredictionIntervals:
    """Interval bounds for a single horizon step or aggregated trajectory."""

    lower: float
    central: float
    upper: float


@dataclass
class ModelPrediction:
    """Standardized prediction output returned by all ModelAdapters."""

    point_forecast: list[float]
    intervals: list[PredictionIntervals]
    range_lower: float
    range_upper: float
    central_estimate: float
    exceedance_probability: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ModelAdapter(Protocol):
    """Protocol defining standard model forecasting interface."""

    @property
    def name(self) -> str:
        """Adapter unique name identifier."""
        ...

    @property
    def config(self) -> dict[str, Any]:
        """Hyperparameters and configuration dictionary."""
        ...

    def get_config_hash(self) -> str:
        """Return SHA-256 hash of adapter configuration."""
        ...

    def fit(self, x: pd.DataFrame, y: pd.Series, as_of: datetime) -> "ModelAdapter":
        """Fit adapter to historical features x and target time-series y up to as_of."""
        ...

    def predict(
        self,
        horizon: int,
        capacity_threshold: float | None = None,
        probability_method: str = "empirical",
    ) -> ModelPrediction:
        """Produce point forecasts, intervals, and threshold exceedance probabilities."""
        ...


def compute_config_hash(config: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hash from config dictionary."""
    serialized = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def calculate_exceedance_probability(
    point_forecast: np.ndarray,
    residuals: np.ndarray,
    capacity_threshold: float,
    method: str = "empirical",
) -> float:
    """Compute P(max(demand) >= capacity_threshold) over horizon.

    Methods:
    - 'empirical': Uses empirical quantile distribution of in-sample residuals.
    - 'normal': Assumes Gaussian distributed residuals N(0, sigma^2).
    """
    if len(residuals) == 0:
        return 0.0 if np.max(point_forecast) < capacity_threshold else 1.0

    if method == "normal":
        sigma = float(np.std(residuals))
        if sigma <= 1e-6:
            return 1.0 if np.max(point_forecast) >= capacity_threshold else 0.0

        step_probs = [
            float(1.0 - norm.cdf((capacity_threshold - mu) / sigma))
            for mu in point_forecast
        ]
        prob_none = np.prod([1.0 - p for p in step_probs])
        prob_exceed = float(1.0 - prob_none)
        return float(np.clip(prob_exceed, 0.0, 1.0))

    num_simulations = 1000
    rng = np.random.default_rng(42)
    bootstrapped_residuals = rng.choice(residuals, size=(num_simulations, len(point_forecast)))
    simulated_trajectories = point_forecast + bootstrapped_residuals
    exceeded_mask = np.any(simulated_trajectories >= capacity_threshold, axis=1)
    return float(np.mean(exceeded_mask))
