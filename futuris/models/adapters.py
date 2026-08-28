"""StatsForecast model adapters: Naive, SeasonalNaive, Drift, AutoETS, AutoARIMA, MeanEnsemble."""

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from statsforecast.models import AutoARIMA as _SF_AutoARIMA
from statsforecast.models import AutoETS as _SF_AutoETS
from statsforecast.models import Naive as _SF_Naive
from statsforecast.models import RandomWalkWithDrift as _SF_RandomWalkWithDrift
from statsforecast.models import SeasonalNaive as _SF_SeasonalNaive

from futuris.models.base import (
    ModelPrediction,
    PredictionIntervals,
    calculate_exceedance_probability,
    compute_config_hash,
)


class BaseStatsForecastAdapter:
    """Base wrapper for single-series statsforecast model estimators."""

    def __init__(self, name: str, config: dict[str, Any] | None = None) -> None:
        self._name = name
        self._config = config or {}
        self.fitted_model: Any | None = None
        self.y_history: np.ndarray | None = None
        self.residuals: np.ndarray = np.array([])
        self.as_of: datetime | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    def get_config_hash(self) -> str:
        return compute_config_hash(self._config)

    def _extract_residuals(self, y: np.ndarray) -> None:
        if len(y) > 1:
            in_sample_pred = np.roll(y, 1)
            in_sample_pred[0] = y[0]
            self.residuals = y - in_sample_pred
        else:
            self.residuals = np.array([0.0])

    def _build_prediction(
        self,
        point_forecast: np.ndarray,
        capacity_threshold: float | None = None,
        probability_method: str = "empirical",
    ) -> ModelPrediction:
        std_res = float(np.std(self.residuals)) if len(self.residuals) > 0 else 0.0
        z_90 = 1.645  # 90% prediction interval

        intervals: list[PredictionIntervals] = []
        for i, val in enumerate(point_forecast):
            step_scale = np.sqrt(i + 1)
            step_lower = float(val - (z_90 * std_res * step_scale))
            step_upper = float(val + (z_90 * std_res * step_scale))
            intervals.append(
                PredictionIntervals(
                    lower=step_lower,
                    central=float(val),
                    upper=step_upper,
                )
            )

        range_lower = (
            min(p.lower for p in intervals) if intervals else float(np.min(point_forecast))
        )
        range_upper = (
            max(p.upper for p in intervals) if intervals else float(np.max(point_forecast))
        )
        central = float(np.mean(point_forecast))

        prob: float | None = None
        if capacity_threshold is not None:
            prob = calculate_exceedance_probability(
                point_forecast=point_forecast,
                residuals=self.residuals,
                capacity_threshold=capacity_threshold,
                method=probability_method,
            )

        return ModelPrediction(
            point_forecast=[float(v) for v in point_forecast],
            intervals=intervals,
            range_lower=round(range_lower, 2),
            range_upper=round(range_upper, 2),
            central_estimate=round(central, 2),
            exceedance_probability=prob,
            metadata={"model": self.name, "config_hash": self.get_config_hash()},
        )


class NaiveAdapter(BaseStatsForecastAdapter):
    """Naive persistence forecaster: predicts last observed value forward."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__("naive", config)

    def fit(self, x: pd.DataFrame, y: pd.Series, as_of: datetime) -> "NaiveAdapter":
        _ = x
        self.as_of = as_of
        self.y_history = y.to_numpy(dtype=float)
        self.fitted_model = _SF_Naive().fit(y=self.y_history)
        self._extract_residuals(self.y_history)
        return self

    def predict(
        self,
        horizon: int,
        capacity_threshold: float | None = None,
        probability_method: str = "empirical",
    ) -> ModelPrediction:
        preds = self.fitted_model.predict(h=horizon)["mean"]
        return self._build_prediction(preds, capacity_threshold, probability_method)


class SeasonalNaiveAdapter(BaseStatsForecastAdapter):
    """Seasonal Naive forecaster: repeats past observed seasonal cycle."""

    def __init__(self, season_length: int = 288, config: dict[str, Any] | None = None) -> None:
        cfg = {"season_length": season_length}
        if config:
            cfg.update(config)
        super().__init__("seasonal_naive", cfg)
        self.season_length = season_length

    def fit(self, x: pd.DataFrame, y: pd.Series, as_of: datetime) -> "SeasonalNaiveAdapter":
        _ = x
        self.as_of = as_of
        self.y_history = y.to_numpy(dtype=float)
        eff_season = self.season_length if len(self.y_history) >= self.season_length else 1
        self.fitted_model = _SF_SeasonalNaive(season_length=eff_season).fit(y=self.y_history)

        if len(self.y_history) > eff_season:
            seasonal_lag = np.roll(self.y_history, eff_season)
            self.residuals = self.y_history[eff_season:] - seasonal_lag[eff_season:]
        else:
            self._extract_residuals(self.y_history)
        return self

    def predict(
        self,
        horizon: int,
        capacity_threshold: float | None = None,
        probability_method: str = "empirical",
    ) -> ModelPrediction:
        preds = self.fitted_model.predict(h=horizon)["mean"]
        return self._build_prediction(preds, capacity_threshold, probability_method)


class DriftAdapter(BaseStatsForecastAdapter):
    """Random walk with linear drift forecaster."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__("drift", config)

    def fit(self, x: pd.DataFrame, y: pd.Series, as_of: datetime) -> "DriftAdapter":
        _ = x
        self.as_of = as_of
        self.y_history = y.to_numpy(dtype=float)
        self.fitted_model = _SF_RandomWalkWithDrift().fit(y=self.y_history)
        self._extract_residuals(self.y_history)
        return self

    def predict(
        self,
        horizon: int,
        capacity_threshold: float | None = None,
        probability_method: str = "empirical",
    ) -> ModelPrediction:
        preds = self.fitted_model.predict(h=horizon)["mean"]
        return self._build_prediction(preds, capacity_threshold, probability_method)


class AutoETSAdapter(BaseStatsForecastAdapter):
    """Automatic Exponential Smoothing (ETS) adapter with deterministic settings."""

    def __init__(self, season_length: int = 288, config: dict[str, Any] | None = None) -> None:
        cfg = {"season_length": season_length, "model": "ZZZ"}
        if config:
            cfg.update(config)
        super().__init__("auto_ets", cfg)
        self.season_length = season_length

    def fit(self, x: pd.DataFrame, y: pd.Series, as_of: datetime) -> "AutoETSAdapter":
        _ = x
        self.as_of = as_of
        self.y_history = y.to_numpy(dtype=float)
        eff_season = self.season_length if len(self.y_history) >= 2 * self.season_length else 1
        self.fitted_model = _SF_AutoETS(season_length=eff_season).fit(y=self.y_history)
        self._extract_residuals(self.y_history)
        return self

    def predict(
        self,
        horizon: int,
        capacity_threshold: float | None = None,
        probability_method: str = "empirical",
    ) -> ModelPrediction:
        preds = self.fitted_model.predict(h=horizon)["mean"]
        return self._build_prediction(preds, capacity_threshold, probability_method)


class AutoARIMAAdapter(BaseStatsForecastAdapter):
    """Automatic ARIMA adapter with deterministic search parameters."""

    def __init__(self, season_length: int = 1, config: dict[str, Any] | None = None) -> None:
        cfg = {"season_length": season_length, "max_p": 3, "max_q": 3}
        if config:
            cfg.update(config)
        super().__init__("auto_arima", cfg)
        self.season_length = season_length

    def fit(self, x: pd.DataFrame, y: pd.Series, as_of: datetime) -> "AutoARIMAAdapter":
        _ = x
        self.as_of = as_of
        self.y_history = y.to_numpy(dtype=float)
        eff_season = self.season_length if len(self.y_history) >= 2 * self.season_length else 1
        self.fitted_model = _SF_AutoARIMA(
            season_length=eff_season,
            max_p=self.config.get("max_p", 3),
            max_q=self.config.get("max_q", 3),
        ).fit(y=self.y_history)
        self._extract_residuals(self.y_history)
        return self

    def predict(
        self,
        horizon: int,
        capacity_threshold: float | None = None,
        probability_method: str = "empirical",
    ) -> ModelPrediction:
        preds = self.fitted_model.predict(h=horizon)["mean"]
        return self._build_prediction(preds, capacity_threshold, probability_method)


class MeanEnsembleAdapter:
    """Averages predictions from AutoETS and SeasonalNaive and pools their uncertainty intervals."""

    def __init__(self, season_length: int = 288, config: dict[str, Any] | None = None) -> None:
        cfg = {"season_length": season_length, "components": ["auto_ets", "seasonal_naive"]}
        if config:
            cfg.update(config)
        self._name = "mean_ensemble"
        self._config = cfg
        self.ets_adapter = AutoETSAdapter(season_length=season_length)
        self.snaive_adapter = SeasonalNaiveAdapter(season_length=season_length)
        self.residuals: np.ndarray = np.array([])
        self.as_of: datetime | None = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    def get_config_hash(self) -> str:
        return compute_config_hash(self._config)

    def fit(self, x: pd.DataFrame, y: pd.Series, as_of: datetime) -> "MeanEnsembleAdapter":
        self.as_of = as_of
        self.ets_adapter.fit(x, y, as_of)
        self.snaive_adapter.fit(x, y, as_of)
        self.residuals = 0.5 * (self.ets_adapter.residuals + self.snaive_adapter.residuals)
        return self

    def predict(
        self,
        horizon: int,
        capacity_threshold: float | None = None,
        probability_method: str = "empirical",
    ) -> ModelPrediction:
        p_ets = self.ets_adapter.predict(horizon, capacity_threshold, probability_method)
        p_snaive = self.snaive_adapter.predict(horizon, capacity_threshold, probability_method)

        avg_point = 0.5 * (np.array(p_ets.point_forecast) + np.array(p_snaive.point_forecast))
        range_lower = min(p_ets.range_lower, p_snaive.range_lower)
        range_upper = max(p_ets.range_upper, p_snaive.range_upper)
        central = float(np.mean(avg_point))

        intervals: list[PredictionIntervals] = []
        for int_e, int_s in zip(p_ets.intervals, p_snaive.intervals, strict=True):
            intervals.append(
                PredictionIntervals(
                    lower=min(int_e.lower, int_s.lower),
                    central=0.5 * (int_e.central + int_s.central),
                    upper=max(int_e.upper, int_s.upper),
                )
            )

        prob: float | None = None
        if capacity_threshold is not None:
            prob = calculate_exceedance_probability(
                point_forecast=avg_point,
                residuals=self.residuals,
                capacity_threshold=capacity_threshold,
                method=probability_method,
            )

        return ModelPrediction(
            point_forecast=[float(v) for v in avg_point],
            intervals=intervals,
            range_lower=round(range_lower, 2),
            range_upper=round(range_upper, 2),
            central_estimate=round(central, 2),
            exceedance_probability=prob,
            metadata={"model": self.name, "config_hash": self.get_config_hash()},
        )
