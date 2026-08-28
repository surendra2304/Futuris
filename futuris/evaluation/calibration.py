"""Calibration analysis: binned reliability, hierarchical shrinkage, and conformal intervals."""

from dataclasses import dataclass

import numpy as np


@dataclass
class ReliabilityCurve:
    """Binned reliability diagram data."""

    bin_centers: list[float]
    observed_frequencies: list[float]
    bin_counts: list[int]


class CalibrationAnalyzer:
    """Evaluates probability calibration and applies small-sample hierarchical shrinkage."""

    def __init__(self, shrinkage_k: float = 20.0, num_bins: int = 10) -> None:
        self.shrinkage_k = shrinkage_k
        self.num_bins = num_bins

    def compute_reliability_curve(
        self,
        predicted_probs: list[float],
        actual_outcomes: list[bool],
    ) -> ReliabilityCurve:
        """Compute binned empirical reliability curve."""
        y_prob = np.asarray(predicted_probs, dtype=float)
        y_true = np.asarray(actual_outcomes, dtype=float)

        if len(y_prob) == 0:
            return ReliabilityCurve([], [], [])

        bins = np.linspace(0.0, 1.0, self.num_bins + 1)
        centers: list[float] = []
        observed_freqs: list[float] = []
        counts: list[int] = []

        for i in range(self.num_bins):
            bin_lower, bin_upper = bins[i], bins[i + 1]
            mask = (
                (y_prob >= bin_lower) & (y_prob < bin_upper)
                if i < self.num_bins - 1
                else (y_prob >= bin_lower) & (y_prob <= bin_upper)
            )
            count = int(np.sum(mask))
            center = float(0.5 * (bin_lower + bin_upper))
            obs_freq = float(np.mean(y_true[mask])) if count > 0 else 0.0

            centers.append(center)
            observed_freqs.append(round(obs_freq, 4))
            counts.append(count)

        return ReliabilityCurve(
            bin_centers=centers,
            observed_frequencies=observed_freqs,
            bin_counts=counts,
        )

    def apply_hierarchical_shrinkage(
        self,
        n_target: int,
        target_observed_rate: float,
        pooled_observed_rate: float,
        k: float | None = None,
    ) -> float:
        """Shrink target-level empirical rate toward the global pooled prior.

        Formula: target_estimate = (n_target * target_rate + k * pooled_rate) / (n_target + k)
        """
        shrink_k = k if k is not None else self.shrinkage_k
        if n_target + shrink_k == 0:
            return pooled_observed_rate

        shrunk = (n_target * target_observed_rate + shrink_k * pooled_observed_rate) / (
            n_target + shrink_k
        )
        return float(np.clip(shrunk, 0.0, 1.0))

    def apply_conformal_interval_scaling(
        self,
        nominal_lower: float,
        nominal_upper: float,
        prediction: float,
        backtest_residuals: list[float],
        desired_coverage: float = 0.90,
    ) -> tuple[float, float]:
        """Conformally adjust interval width using empirical residual quantiles."""
        if not backtest_residuals:
            return nominal_lower, nominal_upper

        abs_res = np.abs(np.asarray(backtest_residuals, dtype=float))
        # Find empirical quantile for desired coverage
        q_level = min(1.0, max(0.0, desired_coverage))
        conformal_radius = float(np.quantile(abs_res, q_level))

        adjusted_lower = float(prediction - conformal_radius)
        adjusted_upper = float(prediction + conformal_radius)
        return adjusted_lower, adjusted_upper
