"""DriverAnalyzer: Feature importance, lead/lag correlation, and driver degradation tracking."""

from dataclasses import dataclass
from uuid import UUID

import numpy as np
import pandas as pd

from futuris.core.schemas import Driver


@dataclass
class DriverEvaluation:
    """Evaluated driver with empirical metrics and degradation status."""

    driver: Driver
    optimal_lag_minutes: int
    long_run_correlation: float
    recent_correlation: float
    is_degraded: bool = False


class DriverAnalyzer:
    """Extracts explanatory drivers, computes lead/lag, and detects correlation degradation."""

    def __init__(self, max_lag_steps: int = 12, step_minutes: int = 5) -> None:
        self.max_lag_steps = max_lag_steps
        self.step_minutes = step_minutes

    def compute_lead_lag(
        self,
        feature_series: pd.Series,
        target_series: pd.Series,
    ) -> tuple[str, int, float]:
        """Compute cross-correlation across lags to classify leading vs lagging."""
        clean_df = pd.DataFrame({"feat": feature_series, "target": target_series}).dropna()
        if len(clean_df) < 20:
            return "lagging", 0, 0.0

        f_vals = clean_df["feat"].to_numpy()
        t_vals = clean_df["target"].to_numpy()

        corrs: dict[int, float] = {}
        for lag in range(0, self.max_lag_steps + 1):
            if lag == 0:
                c = np.corrcoef(f_vals, t_vals)[0, 1]
            else:
                c = np.corrcoef(f_vals[:-lag], t_vals[lag:])[0, 1]
            corrs[lag] = float(0.0 if np.isnan(c) else c)

        best_lag = max(corrs.keys(), key=lambda k: abs(corrs[k]))
        peak_corr = corrs[best_lag]

        classification = "leading" if best_lag > 0 and abs(peak_corr) > 0.15 else "lagging"
        return classification, best_lag * self.step_minutes, peak_corr

    def evaluate_driver_degradation(
        self,
        feature_series: pd.Series,
        target_series: pd.Series,
        recent_window_steps: int = 288,
    ) -> tuple[float, float, bool]:
        """Check if recent correlation dropped by >50% vs long-run mean."""
        clean_df = pd.DataFrame({"feat": feature_series, "target": target_series}).dropna()
        if len(clean_df) < recent_window_steps * 2:
            return 1.0, 1.0, False

        long_corr = abs(float(np.corrcoef(clean_df["feat"], clean_df["target"])[0, 1]))
        if np.isnan(long_corr):
            long_corr = 0.0

        recent_slice = clean_df.iloc[-recent_window_steps:]
        recent_corr = abs(float(np.corrcoef(recent_slice["feat"], recent_slice["target"])[0, 1]))
        if np.isnan(recent_corr):
            recent_corr = 0.0

        is_degraded = (long_corr > 0.20) and (recent_corr < 0.50 * long_corr)
        return round(long_corr, 4), round(recent_corr, 4), is_degraded

    def analyze_drivers(
        self,
        features_df: pd.DataFrame,
        target_column: str,
        evidence_id: UUID,
    ) -> list[Driver]:
        """Analyze features and return ranked drivers with degraded drivers excluded from top."""
        if target_column not in features_df.columns:
            return []

        target_series = features_df[target_column]
        drivers_evaluated: list[DriverEvaluation] = []

        for col in features_df.columns:
            if col == target_column:
                continue

            feat_series = features_df[col]
            if not np.issubdtype(feat_series.dtype, np.number):
                continue

            lead_lag, lag_mins, corr = self.compute_lead_lag(feat_series, target_series)
            long_corr, rec_corr, is_degraded = self.evaluate_driver_degradation(
                feat_series, target_series
            )

            direction = "positive" if corr >= 0 else "negative"
            strength = round(float(min(1.0, max(0.0, abs(corr)))), 2)

            driver_obj = Driver(
                name=col,
                direction=direction,
                strength=strength,
                leading_or_lagging=lead_lag,
                evidence_refs=[evidence_id],
            )

            drivers_evaluated.append(
                DriverEvaluation(
                    driver=driver_obj,
                    optimal_lag_minutes=lag_mins,
                    long_run_correlation=long_corr,
                    recent_correlation=rec_corr,
                    is_degraded=is_degraded,
                )
            )

        valid_drivers = [d.driver for d in drivers_evaluated if not d.is_degraded]
        degraded_drivers = [d.driver for d in drivers_evaluated if d.is_degraded]

        valid_drivers.sort(key=lambda d: d.strength, reverse=True)
        degraded_drivers.sort(key=lambda d: d.strength, reverse=True)

        return valid_drivers + degraded_drivers
