"""DecisionSupport: Advisory implications and action suggestions WITHOUT execution authority.

ARCHITECTURAL SAFETY BOUNDARY:
This module translates probabilistic forecasts into decision-relevant operational advice.
Prediction does NOT equal authorization. This module contains ZERO execution paths, ZERO connectors,
and ZERO HTTP/webhook communication capabilities. All output suggestions are strictly advisory and
require human/governance approval for execution.
"""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from futuris.core.schemas import Forecast


class ActionSuggestion(BaseModel):
    """Advisory recommendation for human operators with hardcoded requires_approval."""

    suggestion_id: UUID = Field(default_factory=uuid4)
    action_type: str
    target: str
    rationale: str
    estimated_mitigation_effect: str
    requires_approval: bool = True  # Hardcoded structural safety gate
    recommended_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DecisionImplication(BaseModel):
    """Decision context translating probability and drivers into urgency and watch lists."""

    target: str
    forecast_id: UUID
    urgency: Literal["now", "today", "this_week", "monitor"]
    expected_impact: str
    key_uncertainties: list[str]
    watch_list: list[str]  # Top sensitive drivers to monitor closely
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DecisionSupport:
    """Produces decision implications and approval-gated action suggestions from forecasts."""

    def implications(
        self,
        forecast: Forecast,
        capacity_threshold: float = 4000.0,
    ) -> DecisionImplication:
        """Derive decision urgency, impact estimation, and driver watch lists."""
        prob = forecast.probability or 0.0
        time_to_expiry = forecast.expires_at - forecast.as_of

        # 1. Determine Urgency Bucket
        if prob >= 0.75 and time_to_expiry <= timedelta(hours=6):
            urgency = "now"
        elif prob >= 0.50 and time_to_expiry <= timedelta(hours=24):
            urgency = "today"
        elif prob >= 0.30:
            urgency = "this_week"
        else:
            urgency = "monitor"

        # 2. Expected Impact Translation
        expected_shortfall = max(0.0, forecast.prediction - capacity_threshold)
        if prob >= 0.50:
            impact_desc = (
                f"High risk ({prob*100:.0f}% chance) of capacity breach. Projected demand "
                f"reaches {forecast.prediction:.0f} vs threshold {capacity_threshold:.0f} "
                f"(shortfall ~{expected_shortfall:.0f} rpm)."
            )
        else:
            impact_desc = (
                f"Moderate/Low risk ({prob*100:.0f}% chance). Baseline projection remains "
                f"bounded within {forecast.range_lower:.0f} - {forecast.range_upper:.0f} rpm."
            )

        # 3. Watch List from top drivers
        if forecast.drivers:
            watch_list = [d.name for d in forecast.drivers[:2]]
        else:
            watch_list = ["traffic_volume"]

        # 4. Key Uncertainties
        uncertainties = list(forecast.assumptions)
        if forecast.confidence == "low":
            uncertainties.append("Model confidence is low due to sample sparsity or variance.")

        return DecisionImplication(
            target=forecast.target,
            forecast_id=forecast.forecast_id,
            urgency=urgency,
            expected_impact=impact_desc,
            key_uncertainties=uncertainties,
            watch_list=watch_list,
        )

    def recommendations(
        self,
        forecast: Forecast,
        policy_config: dict[str, list[str]] | None = None,
    ) -> list[ActionSuggestion]:
        """Generate advisory action suggestions. All high-impact items require approval."""
        _ = policy_config
        prob = forecast.probability or 0.0
        suggestions: list[ActionSuggestion] = []

        if prob >= 0.60:
            suggestions.append(
                ActionSuggestion(
                    action_type="scale_capacity",
                    target=forecast.target,
                    rationale=f"Forecast exceedance is {prob*100:.1f}%. Scaling absorbs peak.",
                    estimated_mitigation_effect="Reduces exceedance risk to < 5%",
                    requires_approval=True,
                )
            )
            suggestions.append(
                ActionSuggestion(
                    action_type="enable_traffic_shedding_policy",
                    target=forecast.target,
                    rationale="Arm degraded non-critical rate-limiting if demand surges past 95%.",
                    estimated_mitigation_effect="Prevents cascading service degradation",
                    requires_approval=True,
                )
            )
        elif prob >= 0.30:
            suggestions.append(
                ActionSuggestion(
                    action_type="prepare_warm_standby",
                    target=forecast.target,
                    rationale="Moderate spike risk. Pre-warming reduces spin-up latency.",
                    estimated_mitigation_effect="Enables instant scaling within 60s",
                    requires_approval=True,
                )
            )
        else:
            suggestions.append(
                ActionSuggestion(
                    action_type="continue_monitoring",
                    target=forecast.target,
                    rationale="Projected demand within safe operational envelope.",
                    estimated_mitigation_effect="N/A",
                    requires_approval=False,
                )
            )

        return suggestions


decision_support = DecisionSupport()
