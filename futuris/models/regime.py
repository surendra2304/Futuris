"""Market regime classification and regime transition forecasting."""

from dataclasses import dataclass
from enum import StrEnum

import numpy as np


class MarketRegime(StrEnum):
    TRENDING = "trending"
    RANGING = "ranging"
    VOLATILE = "volatile"


@dataclass
class RegimeDetectionResult:
    current_regime: MarketRegime
    confidence: float
    volatility_zscore: float
    trend_strength: float
    transition_probabilities: dict[MarketRegime, float]


class MarketRegimeForecaster:
    """Detects market regimes from time-series features and forecasts transition risks."""

    def __init__(
        self,
        volatility_threshold_z: float = 1.8,
        adx_trend_threshold: float = 25.0,
    ) -> None:
        self.volatility_threshold_z = volatility_threshold_z
        self.adx_trend_threshold = adx_trend_threshold

    def detect_regime(
        self,
        prices: list[float],
        volatilities: list[float] | None = None,
    ) -> RegimeDetectionResult:
        """Detect current regime from price and volatility series."""
        p_arr = np.asarray(prices, dtype=float)
        if len(p_arr) < 5:
            return RegimeDetectionResult(
                current_regime=MarketRegime.RANGING,
                confidence=0.5,
                volatility_zscore=0.0,
                trend_strength=15.0,
                transition_probabilities={
                    MarketRegime.TRENDING: 0.33,
                    MarketRegime.RANGING: 0.34,
                    MarketRegime.VOLATILE: 0.33,
                },
            )

        # 1. Compute price returns & trend strength
        returns = np.diff(p_arr) / p_arr[:-1]
        mean_ret = np.mean(returns)
        std_ret = np.std(returns) + 1e-8
        t_stat = abs(mean_ret) / (std_ret / np.sqrt(len(returns)))
        trend_strength = float(min(100.0, t_stat * 15.0))

        # 2. Volatility z-score
        if volatilities and len(volatilities) > 3:
            v_arr = np.asarray(volatilities, dtype=float)
            v_mean = np.mean(v_arr)
            v_std = np.std(v_arr) + 1e-8
            vol_z = float((v_arr[-1] - v_mean) / v_std)
        else:
            vol_z = (
                float(np.std(returns[-10:]) / std_ret)
                if len(returns) >= 10
                else 0.5
            )

        # 3. Regime classification
        if vol_z > self.volatility_threshold_z:
            regime = MarketRegime.VOLATILE
            conf = min(0.95, 0.6 + vol_z * 0.1)
            trans_probs = {
                MarketRegime.VOLATILE: 0.55,
                MarketRegime.TRENDING: 0.25,
                MarketRegime.RANGING: 0.20,
            }
        elif trend_strength > self.adx_trend_threshold:
            regime = MarketRegime.TRENDING
            conf = min(0.92, 0.5 + trend_strength / 150.0)
            trans_probs = {
                MarketRegime.TRENDING: 0.65,
                MarketRegime.VOLATILE: 0.20,
                MarketRegime.RANGING: 0.15,
            }
        else:
            regime = MarketRegime.RANGING
            conf = 0.80
            trans_probs = {
                MarketRegime.RANGING: 0.60,
                MarketRegime.TRENDING: 0.25,
                MarketRegime.VOLATILE: 0.15,
            }

        return RegimeDetectionResult(
            current_regime=regime,
            confidence=round(conf, 3),
            volatility_zscore=round(vol_z, 3),
            trend_strength=round(trend_strength, 2),
            transition_probabilities=trans_probs,
        )

    def route_model_for_regime(self, regime: MarketRegime) -> str:
        """Route to the optimal statistical model adapter tailored for current regime."""
        if regime == MarketRegime.VOLATILE:
            # GARCH/AutoETS handles non-linear dispersion better
            return "auto_ets@v1"
        if regime == MarketRegime.TRENDING:
            # AutoARIMA or Drift tracks directional persistence best
            return "auto_arima@v1"
        # SeasonalNaive or Naive mean reversion performs best in ranging consolidation
        return "seasonal_naive@v1"
