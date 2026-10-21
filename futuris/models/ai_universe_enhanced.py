"""AI-Universe enhanced forecasting models and human-readable explanation generation."""

from dataclasses import dataclass
from math import isfinite
from typing import Any

from futuris.core.schemas import Forecast


@dataclass
class EnhancedForecastContext:
    adjusted_lower_bound: float
    adjusted_upper_bound: float
    qualitative_nuance: str
    risk_factors: list[str]
    confidence_interval_expansion_pct: float


class AIUniverseModelEnhancer:
    """Adjust intervals using explicit exogenous multipliers without inventing risks."""

    def __init__(self, confidence_scaling: float = 1.15) -> None:
        if (
            isinstance(confidence_scaling, bool)
            or not isinstance(confidence_scaling, (int, float))
            or not isfinite(confidence_scaling)
            or confidence_scaling < 1.0
        ):
            raise ValueError("confidence_scaling must be a finite multiplier >= 1.0")
        self.confidence_scaling = float(confidence_scaling)

    def enhance_forecast(
        self,
        base_prediction: float,
        range_lower: float,
        range_upper: float,
        context_factors: dict[str, Any] | None = None,
    ) -> EnhancedForecastContext:
        """Widen the input interval using configured and observed exogenous factors.

        The point prediction is never changed. Context factors must be positive
        finite multipliers. Their magnitudes are reported; no underlying cause is
claimed unless that cause is present in the source data.
        """
        values = (base_prediction, range_lower, range_upper)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            for value in values
        ):
            raise ValueError("forecast estimate and bounds must be finite numbers")
        base_prediction, range_lower, range_upper = (float(value) for value in values)
        if not range_lower <= base_prediction <= range_upper:
            raise ValueError(
                "forecast bounds must satisfy range_lower <= prediction <= range_upper"
            )

        ctx = context_factors or {}
        try:
            vol_factor = float(ctx.get("volatility_multiplier", 1.0))
            sentiment = float(ctx.get("sentiment_multiplier", 1.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("context multipliers must be numeric") from exc
        if not isfinite(vol_factor) or vol_factor <= 0.0:
            raise ValueError("volatility_multiplier must be finite and greater than zero")
        if not isfinite(sentiment) or sentiment <= 0.0:
            raise ValueError("sentiment_multiplier must be finite and greater than zero")

        lower_width = base_prediction - range_lower
        upper_width = range_upper - base_prediction
        total_multiplier = self.confidence_scaling * max(1.0, vol_factor)
        adj_lower = base_prediction - lower_width * total_multiplier
        adj_upper = base_prediction + upper_width * total_multiplier
        original_width = lower_width + upper_width
        expansion_pct = (
            (total_multiplier - 1.0) * 100.0 if original_width > 0.0 else 0.0
        )

        risk_list: list[str] = []
        if vol_factor > 1.0:
            risk_list.append(
                f"Volatility multiplier increased uncertainty width by factor "
                f"{vol_factor:.2f}"
            )
        if sentiment < 1.0:
            risk_list.append(
                f"Adverse sentiment multiplier was supplied ({sentiment:.2f}); "
                "specific cause not inferred"
            )
        elif sentiment > 1.0:
            risk_list.append(
                f"Favourable sentiment multiplier was supplied ({sentiment:.2f}); "
                "outcome certainty is not implied"
            )
        if not risk_list:
            risk_list.append("No additional exogenous risk modifier was reported")

        nuance_text = (
            f"Statistical point estimate preserved at {base_prediction:.2f}. "
            f"Uncertainty half-width scaled by factor {total_multiplier:.2f}."
        )
        return EnhancedForecastContext(
            adjusted_lower_bound=round(adj_lower, 2),
            adjusted_upper_bound=round(adj_upper, 2),
            qualitative_nuance=nuance_text,
            risk_factors=risk_list,
            confidence_interval_expansion_pct=round(expansion_pct, 1),
        )

class ForecastExplanationGenerator:
    """Human-readable explanations with explicit boundaries for missing evidence."""

    @staticmethod
    def generate_narrative_explanation(
        forecast: Forecast,
        historical_accuracy_pct: float | None = None,
    ) -> str:
        """Explain a forecast without inventing drivers, risks, or quality metrics.

        Historical accuracy is optional because the Forecast object does not
        itself prove historical accuracy. Callers may provide it only when it
        has been computed from resolved outcomes for the relevant target.
        """
        if historical_accuracy_pct is not None and (
            isinstance(historical_accuracy_pct, bool)
            or not isinstance(historical_accuracy_pct, (int, float))
            or not isfinite(historical_accuracy_pct)
            or not 0.0 <= historical_accuracy_pct <= 100.0
        ):
            raise ValueError(
                "historical_accuracy_pct must be a finite percentage in [0, 100]"
            )

        driver_text = "not identified from the available forecast evidence"
        if forecast.drivers:
            top_driver = forecast.drivers[0]
            direction = (
                top_driver.direction.value
                if hasattr(top_driver.direction, 'value')
                else str(top_driver.direction)
            )
            driver_text = f"{top_driver.name} ({direction})"
            if isfinite(float(top_driver.strength)):
                # Strength is a relative influence weight, not a measured correlation.
                driver_text += f", reported strength {float(top_driver.strength):.2f}"

        probability_text = (
            f", event probability {forecast.probability * 100:.1f}%"
            if forecast.probability is not None
            else ""
        )
        calibration_text = (
            "available in forecast metadata"
            if forecast.calibration_metrics
            else "not measured from resolved outcomes"
        )
        accuracy_text = (
            f"{historical_accuracy_pct:.1f}% (caller-supplied measured value)"
            if historical_accuracy_pct is not None
            else "not measured for this target"
        )
        assumptions_text = (
            "; ".join(forecast.assumptions) if forecast.assumptions else "not recorded"
        )

        return (
            f"{forecast.target} forecast at {forecast.prediction:.2f} "
            f"(reported prediction interval: {forecast.range_lower:.2f} to "
            f"{forecast.range_upper:.2f}{probability_text}). "
            f"Identified driver: {driver_text}. "
            f"Model: {forecast.model_version}. "
            f"Meta-confidence: {forecast.confidence.value}. "
            f"Calibration metrics: {calibration_text}. "
            f"Historical accuracy: {accuracy_text}. "
            f"Recorded assumptions: {assumptions_text}. "
            "Forecasts are advisory and are not authorization to act."
        )
