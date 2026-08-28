"""Deterministic rule-based ModelRouter selecting candidate adapters based on series metadata."""

from dataclasses import dataclass

from futuris.features.normalize import DataQualityReport


@dataclass(frozen=True)
class SeriesMetadata:
    """Statistical and temporal metadata characterizing a time-series."""

    history_points: int
    frequency_minutes: int
    has_daily_seasonality: bool = True
    has_weekly_seasonality: bool = True


class ModelRouter:
    """Pure, deterministic heuristic router selecting and ranking forecasting models."""

    def select_candidates(
        self,
        metadata: SeriesMetadata,
        horizon_steps: int,
        quality_report: DataQualityReport | None = None,
    ) -> list[str]:
        """Return a ranked list of candidate adapter names for the series.

        Heuristic Routing Policy:
        1. Base: Always include 'naive' as a sanity baseline.
        2. Short history (< 288 points, e.g. < 1 day at 5m): Route only to ['naive', 'drift'].
        3. Low coverage (< 80%): Prefer robust baselines ['naive', 'drift', 'seasonal_naive'].
        4. Rich history (>= 2016 points, e.g. >= 1 week at 5m) + strong seasonality:
           Rank ['mean_ensemble', 'auto_ets', 'seasonal_naive', 'drift', 'naive'].
        5. Moderate history (288 to 2015 points):
           Rank ['seasonal_naive', 'auto_ets', 'drift', 'naive'].
        """
        _ = horizon_steps

        # Policy Branch 1: Short history
        if metadata.history_points < 288:
            return ["drift", "naive"]

        # Policy Branch 2: Degraded data coverage
        if quality_report and quality_report.coverage_percentage < 80.0:
            return ["seasonal_naive", "drift", "naive"]

        # Policy Branch 3: Rich history with weekly seasonality
        if metadata.history_points >= 2016 and metadata.has_weekly_seasonality:
            return ["mean_ensemble", "auto_ets", "seasonal_naive", "drift", "naive"]

        # Policy Branch 4: Moderate history (>= 1 day)
        return ["seasonal_naive", "auto_ets", "drift", "naive"]
