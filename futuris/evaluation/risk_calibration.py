"""Quantitative Value-at-Risk (VaR), Expected Shortfall (CVaR), and Kupiec/Christoffersen tests."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from scipy.stats import binom, chi2, norm


@dataclass(frozen=True)
class RiskMetrics:
    """Tail risk and extreme shortfall summary."""

    confidence_level: float
    var_parametric: float
    var_historical: float
    cvar_expected_shortfall: float
    sample_size: int
    breach_count: int
    breach_rate: float
    expected_breach_rate: float


@dataclass(frozen=True)
class BacktestResult:
    """Statistical hypothesis test results for risk calibration."""

    test_name: str
    statistic: float
    p_value: float
    is_rejected: bool
    confidence_level: float
    interpretation: str


@dataclass(frozen=True)
class RiskCalibrationReport:
    """Full quantitative tail risk calibration assessment."""

    metrics_95: RiskMetrics
    metrics_99: RiskMetrics
    kupiec_test_95: BacktestResult
    kupiec_test_99: BacktestResult
    christoffersen_independence_test: BacktestResult
    christoffersen_conditional_coverage: BacktestResult
    basel_traffic_light: Literal["green", "yellow", "red"]
    calibration_summary: dict[str, Any]


class RiskCalibrationEngine:
    """Engine computing VaR, CVaR/Expected Shortfall, and Kupiec/Christoffersen tests."""

    def __init__(self, significance_threshold: float = 0.05) -> None:
        self.significance_threshold = significance_threshold

    def calculate_var_cvar(
        self,
        returns_or_residuals: np.ndarray | list[float],
        confidence_level: float = 0.95,
    ) -> RiskMetrics:
        """Compute parametric and historical VaR along with Expected Shortfall (CVaR).

        By convention in risk modeling, losses are positive values: loss = -return.
        VaR is the threshold such that P(loss > VaR) = 1 - confidence_level.
        """
        arr = np.asarray(returns_or_residuals, dtype=float)
        n = len(arr)
        if n == 0:
            msg = "Empty array passed to calculate_var_cvar."
            raise ValueError(msg)

        # Work in loss space: positive values represent losses
        losses = -arr if np.mean(arr) < 0 else arr
        alpha = 1.0 - confidence_level

        # 1. Historical VaR: (1 - alpha) quantile of losses
        var_hist = float(np.percentile(losses, confidence_level * 100.0))

        # 2. Parametric Gaussian VaR
        mu = float(np.mean(losses))
        std = float(np.std(losses))
        z_crit = float(norm.ppf(confidence_level))
        var_param = float(mu + z_crit * std)

        # 3. Conditional Value at Risk (Expected Shortfall): average loss exceeding VaR
        exceedances = losses[losses >= var_hist]
        cvar = float(np.mean(exceedances)) if len(exceedances) > 0 else var_hist

        breaches = int(np.sum(losses > var_hist))
        breach_rate = breaches / max(1, n)

        return RiskMetrics(
            confidence_level=round(confidence_level, 4),
            var_parametric=round(var_param, 4),
            var_historical=round(var_hist, 4),
            cvar_expected_shortfall=round(cvar, 4),
            sample_size=n,
            breach_count=breaches,
            breach_rate=round(breach_rate, 4),
            expected_breach_rate=round(alpha, 4),
        )

    def kupiec_pof_test(
        self,
        breach_count: int,
        total_observations: int,
        confidence_level: float = 0.95,
    ) -> BacktestResult:
        """Kupiec Proportion of Failures (POF) Likelihood Ratio test.

        H0: Actual breach probability p_hat equals theoretical probability p = (1 - confidence).
        Statistic LR_pof = -2 * ln( L(p) / L(p_hat) ) ~ Chi2(1).
        """
        n = total_observations
        x = breach_count
        p = 1.0 - confidence_level

        if n <= 0:
            msg = "total_observations must be strictly positive."
            raise ValueError(msg)

        p_hat = x / n

        # Boundary / edge case handling for likelihood ratio
        if x == 0:
            lr_pof = -2.0 * n * math.log(max(1e-12, 1.0 - p))
        elif x == n:
            lr_pof = -2.0 * n * math.log(max(1e-12, p))
        elif math.isclose(p_hat, p, rel_tol=1e-5):
            lr_pof = 0.0
        else:
            term1 = (n - x) * math.log((1.0 - p) / max(1e-12, 1.0 - p_hat))
            term2 = x * math.log(p / max(1e-12, p_hat))
            lr_pof = -2.0 * (term1 + term2)

        lr_pof = max(0.0, lr_pof)
        p_val = float(1.0 - chi2.cdf(lr_pof, df=1))
        rejected = p_val < self.significance_threshold

        interp = (
            f"Model risk calibration rejected at alpha={self.significance_threshold}: "
            f"breaches ({x}/{n}) diverge statistically from theoretical rate {p:.2%}"
            if rejected
            else f"Model risk calibration well-calibrated (p={p_val:.4f}): "
            f"observed breach rate {p_hat:.2%} aligns with {p:.2%}"
        )

        return BacktestResult(
            test_name=f"Kupiec POF ({int(confidence_level * 100)}%)",
            statistic=round(lr_pof, 4),
            p_value=round(p_val, 4),
            is_rejected=rejected,
            confidence_level=confidence_level,
            interpretation=interp,
        )

    def christoffersen_independence_test(
        self,
        breach_series: np.ndarray | list[int] | list[bool],
    ) -> BacktestResult:
        """Christoffersen Likelihood Ratio test for exception independence (no breach clustering).

        H0: Exceptions are independently distributed over time: P(breach|breach) == P(breach|no).
        Statistic LR_ind = -2 * ln( L_0 / L_1 ) ~ Chi2(1).
        """
        arr = np.asarray(breach_series, dtype=int)
        n = len(arr)
        if n < 4:
            return BacktestResult(
                test_name="Christoffersen Independence Test",
                statistic=0.0,
                p_value=1.0,
                is_rejected=False,
                confidence_level=0.95,
                interpretation="Insufficient sample for clustering analysis.",
            )

        # Transition counts: n00, n01, n10, n11
        n00, n01, n10, n11 = 0, 0, 0, 0
        for t in range(n - 1):
            curr, nxt = arr[t], arr[t + 1]
            if curr == 0 and nxt == 0:
                n00 += 1
            elif curr == 0 and nxt == 1:
                n01 += 1
            elif curr == 1 and nxt == 0:
                n10 += 1
            elif curr == 1 and nxt == 1:
                n11 += 1

        total = n00 + n01 + n10 + n11
        pi_0 = n01 / max(1, n00 + n01) if (n00 + n01) > 0 else 0.0
        pi_1 = n11 / max(1, n10 + n11) if (n10 + n11) > 0 else 0.0
        pi = (n01 + n11) / max(1, total)

        # Compute log-likelihood under independence L0 vs Markovian L1
        def _safe_log(val: float) -> float:
            return math.log(max(1e-12, val))

        log_l0 = (n00 + n10) * _safe_log(1.0 - pi) + (n01 + n11) * _safe_log(pi)
        log_l1 = (
            n00 * _safe_log(1.0 - pi_0)
            + n01 * _safe_log(pi_0)
            + n10 * _safe_log(1.0 - pi_1)
            + n11 * _safe_log(pi_1)
        )

        lr_ind = max(0.0, -2.0 * (log_l0 - log_l1))
        p_val = float(1.0 - chi2.cdf(lr_ind, df=1))
        rejected = p_val < self.significance_threshold

        interp = (
            "Breach clustering detected: exceptions occur in bursts violating independence"
            if rejected
            else "Breach independence verified: no volatility clustering anomalies detected"
        )

        return BacktestResult(
            test_name="Christoffersen Independence Test",
            statistic=round(lr_ind, 4),
            p_value=round(p_val, 4),
            is_rejected=rejected,
            confidence_level=0.95,
            interpretation=interp,
        )

    def evaluate_traffic_light(
        self,
        breach_count: int,
        total_observations: int,
        confidence_level: float = 0.99,
    ) -> Literal["green", "yellow", "red"]:
        """Basel traffic light framework based on cumulative binomial breach probability."""
        p = 1.0 - confidence_level
        # Cumulative probability of observing <= breach_count
        prob = float(binom.cdf(breach_count, total_observations, p))

        if prob < 0.95:
            return "green"
        if prob < 0.9999:
            return "yellow"
        return "red"

    def audit_risk_calibration(
        self,
        returns_or_residuals: np.ndarray | list[float],
    ) -> RiskCalibrationReport:
        """Execute full quantitative audit: 95% & 99% VaR/CVaR, POF, and Basel zone."""
        arr = np.asarray(returns_or_residuals, dtype=float)
        losses = -arr if np.mean(arr) < 0 else arr
        n = len(losses)

        m95 = self.calculate_var_cvar(losses, confidence_level=0.95)
        m99 = self.calculate_var_cvar(losses, confidence_level=0.99)

        kupiec_95 = self.kupiec_pof_test(m95.breach_count, n, confidence_level=0.95)
        kupiec_99 = self.kupiec_pof_test(m99.breach_count, n, confidence_level=0.99)

        breach_indicators_95 = (losses > m95.var_historical).astype(int)
        christ_ind = self.christoffersen_independence_test(breach_indicators_95)

        # Combined conditional coverage statistic LR_cc = LR_pof + LR_ind ~ Chi2(2)
        lr_cc = kupiec_95.statistic + christ_ind.statistic
        p_val_cc = float(1.0 - chi2.cdf(lr_cc, df=2))
        cc_result = BacktestResult(
            test_name="Christoffersen Conditional Coverage (Combined)",
            statistic=round(lr_cc, 4),
            p_value=round(p_val_cc, 4),
            is_rejected=p_val_cc < self.significance_threshold,
            confidence_level=0.95,
            interpretation=(
                "Joint coverage and independence test passed"
                if p_val_cc >= self.significance_threshold
                else "Joint coverage or independence test failed"
            ),
        )

        traffic_light = self.evaluate_traffic_light(m99.breach_count, n, confidence_level=0.99)

        summary = {
            "total_samples": n,
            "basel_traffic_light": traffic_light,
            "var_95_historical": m95.var_historical,
            "cvar_95": m95.cvar_expected_shortfall,
            "var_99_historical": m99.var_historical,
            "cvar_99": m99.cvar_expected_shortfall,
            "breaches_95": m95.breach_count,
            "breaches_99": m99.breach_count,
            "risk_advisory": (
                "Adequate risk provisioning"
                if traffic_light == "green"
                else "Elevated risk caution required"
            ),
        }

        return RiskCalibrationReport(
            metrics_95=m95,
            metrics_99=m99,
            kupiec_test_95=kupiec_95,
            kupiec_test_99=kupiec_99,
            christoffersen_independence_test=christ_ind,
            christoffersen_conditional_coverage=cc_result,
            basel_traffic_light=traffic_light,
            calibration_summary=summary,
        )
