"""Global sensitivity analysis (Sobol first & total-order indices and Tornado rankings)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class SobolIndex:
    """Sobol variance decomposition indices for a single input driver."""

    driver_name: str
    first_order: float  # S_i: Main effect contribution to variance
    total_order: float  # S_Ti: Total effect including all interactions
    interaction_effect: float  # S_Ti - S_i


@dataclass(frozen=True)
class TornadoBar:
    """Single bar in a tornado diagram showing sensitivity to swing."""

    driver_name: str
    base_value: float
    low_value: float
    high_value: float
    output_at_low: float
    output_at_high: float
    swing_range: float


@dataclass
class GlobalSensitivityReport:
    """Comprehensive global sensitivity analysis and driver importance ranking."""

    target_name: str
    total_variance: float
    sobol_indices: list[SobolIndex]
    tornado_diagram: list[TornadoBar]
    top_drivers: list[str]
    interaction_present: bool
    metadata: dict[str, Any] = field(default_factory=dict)


class GlobalSensitivityAnalyzer:
    """Computes Sobol variance decomposition indices and deterministic Tornado rankings."""

    def __init__(self, seed: int = 42) -> None:
        self.rng = np.random.default_rng(seed)

    def compute_sobol_indices(
        self,
        driver_ranges: dict[str, tuple[float, float]],
        model_func: Callable[[dict[str, float]], float],
        num_samples: int = 1000,
    ) -> list[SobolIndex]:
        """Compute first-order and total-order Sobol indices via Saltelli/Jansen sampling."""
        names = list(driver_ranges.keys())
        k = len(names)
        if k == 0:
            return []

        # Generate sample matrices A and B in unit hypercube [0, 1]^k
        a_unit = self.rng.uniform(0.0, 1.0, size=(num_samples, k))
        b_unit = self.rng.uniform(0.0, 1.0, size=(num_samples, k))

        # Scale to physical ranges
        def _scale(matrix: np.ndarray) -> np.ndarray:
            scaled = np.zeros_like(matrix)
            for col, name in enumerate(names):
                low, high = driver_ranges[name]
                scaled[:, col] = low + matrix[:, col] * (high - low)
            return scaled

        a_mat = _scale(a_unit)
        b_mat = _scale(b_unit)

        # Evaluate model at A and B
        y_a = np.array([model_func(dict(zip(names, row, strict=True))) for row in a_mat])
        y_b = np.array([model_func(dict(zip(names, row, strict=True))) for row in b_mat])

        var_y = float(np.var(y_a))
        if var_y <= 1e-9:
            # Constant output; zero sensitivity
            return [
                SobolIndex(
                    driver_name=name,
                    first_order=0.0,
                    total_order=0.0,
                    interaction_effect=0.0,
                )
                for name in names
            ]

        results: list[SobolIndex] = []

        for i, name in enumerate(names):
            # Construct A_B^(i): Matrix A with column i replaced by column i of B
            ab_unit = np.copy(a_unit)
            ab_unit[:, i] = b_unit[:, i]
            ab_mat = _scale(ab_unit)

            y_ab = np.array([model_func(dict(zip(names, row, strict=True))) for row in ab_mat])

            # Jansen (1999) estimator for Total Order S_Ti
            # S_Ti = (1 / (2N * Var(Y))) * sum((y_a - y_ab)^2)
            s_total = float(np.mean((y_a - y_ab) ** 2) / (2.0 * var_y))

            # Jansen estimator for First Order S_i
            # S_i = 1 - (1 / (2N * Var(Y))) * sum((y_b - y_ab)^2)
            s_first = float(1.0 - (np.mean((y_b - y_ab) ** 2) / (2.0 * var_y)))

            # Bound neatly in [0, 1]
            s_first = max(0.0, min(1.0, s_first))
            s_total = max(s_first, min(1.0, s_total))
            interaction = max(0.0, s_total - s_first)

            results.append(
                SobolIndex(
                    driver_name=name,
                    first_order=round(s_first, 4),
                    total_order=round(s_total, 4),
                    interaction_effect=round(interaction, 4),
                )
            )

        # Sort by total-order index descending
        results.sort(key=lambda x: x.total_order, reverse=True)
        return results

    def compute_tornado_ranking(
        self,
        base_drivers: dict[str, float],
        model_func: Callable[[dict[str, float]], float],
        swing_pct: float = 0.15,
    ) -> list[TornadoBar]:
        """Compute Tornado diagram bars measuring single-factor swings around baseline."""
        bars: list[TornadoBar] = []

        for name, base_val in base_drivers.items():
            delta = abs(base_val * swing_pct) if abs(base_val) > 1e-4 else 0.1
            low_val = base_val - delta
            high_val = base_val + delta

            d_low = dict(base_drivers)
            d_low[name] = low_val
            out_low = model_func(d_low)

            d_high = dict(base_drivers)
            d_high[name] = high_val
            out_high = model_func(d_high)

            swing_range = abs(out_high - out_low)

            bars.append(
                TornadoBar(
                    driver_name=name,
                    base_value=round(base_val, 4),
                    low_value=round(low_val, 4),
                    high_value=round(high_val, 4),
                    output_at_low=round(out_low, 4),
                    output_at_high=round(out_high, 4),
                    swing_range=round(swing_range, 4),
                )
            )

        # Sort descending by swing range
        bars.sort(key=lambda b: b.swing_range, reverse=True)
        return bars

    def analyze(
        self,
        target_name: str,
        driver_ranges: dict[str, tuple[float, float]],
        base_drivers: dict[str, float],
        model_func: Callable[[dict[str, float]], float],
        num_samples: int = 1000,
        swing_pct: float = 0.15,
    ) -> GlobalSensitivityReport:
        """Run complete global sensitivity study: Sobol variance indices + Tornado ranking."""
        sobol = self.compute_sobol_indices(
            driver_ranges=driver_ranges,
            model_func=model_func,
            num_samples=num_samples,
        )
        tornado = self.compute_tornado_ranking(
            base_drivers=base_drivers,
            model_func=model_func,
            swing_pct=swing_pct,
        )

        top_drivers = [s.driver_name for s in sobol[:3]]
        interaction_present = any(s.interaction_effect > 0.05 for s in sobol)

        sample_inputs = [
            {
                k: float(self.rng.uniform(driver_ranges[k][0], driver_ranges[k][1]))
                for k in driver_ranges
            }
            for _ in range(100)
        ]
        sample_outputs = [model_func(inp) for inp in sample_inputs]
        total_var = float(np.var(sample_outputs))

        return GlobalSensitivityReport(
            target_name=target_name,
            total_variance=round(total_var, 4),
            sobol_indices=sobol,
            tornado_diagram=tornado,
            top_drivers=top_drivers,
            interaction_present=interaction_present,
            metadata={"num_samples": num_samples, "swing_pct": swing_pct},
        )
