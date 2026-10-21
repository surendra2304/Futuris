"""Multivariate Monte Carlo scenario engine with Cholesky correlation and antithetic variates."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from scipy.stats import norm


@dataclass(frozen=True)
class DriverDistribution:
    """Marginal distribution specification for a scenario input driver."""

    name: str
    dist_type: Literal["normal", "lognormal", "uniform"]
    param1: float  # normal/lognormal: mean / log-mean; uniform: lower
    param2: float  # normal/lognormal: std / log-std; uniform: upper


@dataclass(frozen=True)
class FanChartSlice:
    """Fan chart percentile distributions at a discrete time horizon step."""

    step: int
    mean: float
    std: float
    p05: float
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    p95: float
    p99: float
    exceedance_probability: float | None = None


@dataclass
class MonteCarloSimulationResult:
    """Aggregated output from multivariate stochastic simulation."""

    num_samples: int
    horizon_steps: int
    fan_chart: list[FanChartSlice]
    driver_names: list[str]
    sample_correlations: dict[str, dict[str, float]]
    trajectories_sample: list[list[float]] = field(default_factory=list)  # 5 representative paths
    metadata: dict[str, Any] = field(default_factory=dict)


def _ensure_positive_semidefinite(matrix: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """Ensure matrix is strictly positive semi-definite via eigenvalue floor."""
    sym = (matrix + matrix.T) / 2.0
    eigvals, eigvecs = np.linalg.eigh(sym)
    clipped_eigvals = np.maximum(eigvals, eps)
    psd = eigvecs @ np.diag(clipped_eigvals) @ eigvecs.T
    # Normalize diagonals to 1.0 for correlation matrix
    diag_sqrt = np.sqrt(np.diag(psd))
    return psd / np.outer(diag_sqrt, diag_sqrt)


class MonteCarloScenarioEngine:
    """Simulates correlated multivariate stochastic drivers with variance reduction."""

    def __init__(self, seed: int | None = 42) -> None:
        self.seed = seed
        self.rng = np.random.default_rng(seed)

    def sample_correlated_drivers(
        self,
        drivers: list[DriverDistribution],
        correlation_matrix: np.ndarray | None = None,
        num_samples: int = 2000,
        use_antithetic_variates: bool = True,
    ) -> dict[str, np.ndarray]:
        """Sample correlated multivariate drivers using Cholesky decomposition."""
        d = len(drivers)
        if d == 0:
            return {}

        n = num_samples if not use_antithetic_variates else (num_samples // 2) * 2

        # Default to identity correlation if not supplied
        if correlation_matrix is None:
            corr = np.eye(d)
        else:
            corr = _ensure_positive_semidefinite(np.asarray(correlation_matrix, dtype=float))

        # Cholesky factor L such that L * L^T = corr
        try:
            l_factor = np.linalg.cholesky(corr)
        except np.linalg.LinAlgError:
            corr = _ensure_positive_semidefinite(corr, eps=1e-4)
            l_factor = np.linalg.cholesky(corr)

        # Generate standard normal draws
        if use_antithetic_variates:
            half_n = n // 2
            z_half = self.rng.standard_normal(size=(half_n, d))
            z = np.vstack([z_half, -z_half])
        else:
            z = self.rng.standard_normal(size=(n, d))

        # Correlated standard normals
        correlated_z = z @ l_factor.T

        samples: dict[str, np.ndarray] = {}
        for i, driver in enumerate(drivers):
            col_z = correlated_z[:, i]
            if driver.dist_type == "normal":
                samples[driver.name] = driver.param1 + driver.param2 * col_z
            elif driver.dist_type == "lognormal":
                samples[driver.name] = np.exp(driver.param1 + driver.param2 * col_z)
            elif driver.dist_type == "uniform":
                # Probability Integral Transform (PIT)
                uniform_u = norm.cdf(col_z)
                samples[driver.name] = driver.param1 + (driver.param2 - driver.param1) * uniform_u
            else:
                samples[driver.name] = driver.param1 + driver.param2 * col_z

        return samples

    def simulate_trajectories(
        self,
        base_value: float,
        drivers: list[DriverDistribution],
        transfer_function: Callable[[dict[str, float], float, int], float],
        correlation_matrix: np.ndarray | None = None,
        horizon_steps: int = 12,
        num_samples: int = 2000,
        capacity_threshold: float | None = None,
        use_antithetic_variates: bool = True,
    ) -> MonteCarloSimulationResult:
        """Propagate joint stochastic paths across multi-step horizon and compute fan charts."""
        n = num_samples if not use_antithetic_variates else (num_samples // 2) * 2
        trajectories = np.zeros((n, horizon_steps))

        # Generate multi-step correlated innovations
        driver_samples_per_step: list[dict[str, np.ndarray]] = []
        for _ in range(horizon_steps):
            step_samples = self.sample_correlated_drivers(
                drivers=drivers,
                correlation_matrix=correlation_matrix,
                num_samples=n,
                use_antithetic_variates=use_antithetic_variates,
            )
            driver_samples_per_step.append(step_samples)

        # Propagate paths across time steps
        for sim_idx in range(n):
            current_val = base_value
            for step in range(horizon_steps):
                step_drivers = {
                    d.name: float(driver_samples_per_step[step][d.name][sim_idx]) for d in drivers
                }
                current_val = transfer_function(step_drivers, current_val, step)
                trajectories[sim_idx, step] = current_val

        # Compute Fan Chart Percentiles across horizon
        fan_slices: list[FanChartSlice] = []
        for step in range(horizon_steps):
            vals = trajectories[:, step]
            p05, p10, p25, p50, p75, p90, p95, p99 = np.percentile(
                vals, [5, 10, 25, 50, 75, 90, 95, 99]
            )
            prob_exceed = (
                float(np.mean(vals >= capacity_threshold))
                if capacity_threshold is not None
                else None
            )

            fan_slices.append(
                FanChartSlice(
                    step=step + 1,
                    mean=round(float(np.mean(vals)), 2),
                    std=round(float(np.std(vals)), 2),
                    p05=round(float(p05), 2),
                    p10=round(float(p10), 2),
                    p25=round(float(p25), 2),
                    p50=round(float(p50), 2),
                    p75=round(float(p75), 2),
                    p90=round(float(p90), 2),
                    p95=round(float(p95), 2),
                    p99=round(float(p99), 2),
                    exceedance_probability=round(prob_exceed, 4)
                    if prob_exceed is not None
                    else None,
                )
            )

        # Compute empirical correlation matrix from first step
        emp_corr: dict[str, dict[str, float]] = {}
        if driver_samples_per_step:
            step0 = driver_samples_per_step[0]
            d_names = [d.name for d in drivers]
            for d1 in d_names:
                emp_corr[d1] = {}
                for d2 in d_names:
                    r = float(np.corrcoef(step0[d1], step0[d2])[0, 1])
                    emp_corr[d1][d2] = round(0.0 if math.isnan(r) else r, 4)

        # Extract 5 sample trajectories for visualization
        rep_trajectories = [
            [round(float(v), 2) for v in trajectories[idx]]
            for idx in np.linspace(0, n - 1, num=min(5, n), dtype=int)
        ]

        metadata = {
            "antithetic_variates": use_antithetic_variates,
            "seed": self.seed,
            "drivers_count": len(drivers),
            "capacity_threshold": capacity_threshold,
        }

        return MonteCarloSimulationResult(
            num_samples=n,
            horizon_steps=horizon_steps,
            fan_chart=fan_slices,
            driver_names=[d.name for d in drivers],
            sample_correlations=emp_corr,
            trajectories_sample=rep_trajectories,
            metadata=metadata,
        )
