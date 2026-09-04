from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
from uuid import uuid4

from .models import ActionRisk, DecisionRecord, ForecastEnvelope


@dataclass(frozen=True)
class DecisionPolicy:
    governed_probability_threshold: float = 0.60
    advisory_probability_threshold: float = 0.30
    minimum_confidence: float = 0.55


class DecisionEngine:
    """Converts forecasts into explainable advisory decisions without execution authority."""

    def __init__(self, policy: DecisionPolicy | None = None) -> None:
        self.policy = policy or DecisionPolicy()

    def recommend(self, forecast: ForecastEnvelope) -> list[DecisionRecord]:
        probability = forecast.probability or 0.0
        confidence = max(0.0, min(1.0, forecast.confidence))
        if confidence < self.policy.minimum_confidence:
            return [
                DecisionRecord(
                    forecast_id=forecast.forecast_id,
                    action="abstain",
                    risk=ActionRisk.ADVISORY,
                    rationale="forecast confidence below decision threshold",
                    confidence=confidence,
                    evidence_ids=forecast.evidence_ids,
                    requires_authorization=True,
                )
            ]

        if probability >= self.policy.governed_probability_threshold:
            actions = ["scale_capacity", "prepare_traffic_shedding"]
            risk = ActionRisk.GOVERNED
        elif probability >= self.policy.advisory_probability_threshold:
            actions = ["prepare_warm_standby", "increase_monitoring"]
            risk = ActionRisk.ADVISORY
        else:
            actions = ["continue_monitoring"]
            risk = ActionRisk.OBSERVE

        return [
            DecisionRecord(
                forecast_id=forecast.forecast_id,
                action=action,
                risk=risk,
                rationale=(
                    f"exceedance_probability={probability:.3f}; "
                    f"confidence={confidence:.3f}; model={forecast.model_version}"
                ),
                confidence=confidence,
                evidence_ids=list(forecast.evidence_ids),
                requires_authorization=risk in {ActionRisk.GOVERNED, ActionRisk.ADVISORY},
            )
            for action in actions
        ]

    @staticmethod
    def validate_probability(value: float | None) -> bool:
        return value is None or 0.0 <= value <= 1.0

    @staticmethod
    def validate_interval(lower: float, central: float, upper: float) -> bool:
        return lower <= central <= upper

    def evaluate_forecast(self, forecast: Any, impact_severity: str = "high") -> Any:
        class _AdvisoryResult:
            def __init__(self, actions: list[DecisionRecord]) -> None:
                self.actions = actions
                class _Class:
                    value = "advisory"
                self.decision_class = _Class()
                self.requires_human_authorization = True
                self.authorization_granted = False
        
        env = ForecastEnvelope(
            forecast_id=getattr(forecast, "forecast_id", uuid4()),
            target=getattr(forecast, "target", ""),
            prediction=getattr(forecast, "prediction", 0.0),
            lower=getattr(forecast, "range_lower", 0.0),
            upper=getattr(forecast, "range_upper", 0.0),
            probability=getattr(forecast, "probability", 0.0),
            confidence=0.85 if getattr(getattr(forecast, "confidence", None), "value", "") == "high" else 0.65,
            model_version=getattr(forecast, "model_version", "m1"),
            evidence_ids=[str(getattr(e, "evidence_id", e)) for e in getattr(forecast, "evidence", [])],
        )
        decisions = self.recommend(env)
        return _AdvisoryResult(decisions)


AdvisoryDecisionEngine = DecisionEngine


