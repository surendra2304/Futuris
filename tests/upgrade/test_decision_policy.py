"""Decision-policy branches of the advisory engine (futuris/upgrade/decision.py).

The engine turns a forecast into a recommendation and must never grant authority:
every record it produces leaves ``requires_authorization`` set except pure
observation, and no path sets ``authorization_granted``. These tests pin the
thresholds and that invariant.
"""

from __future__ import annotations

import pytest

from futuris.upgrade.decision import AdvisoryDecisionEngine, DecisionEngine, DecisionPolicy
from futuris.upgrade.models import ActionRisk, ForecastEnvelope


def _envelope(*, probability: float | None, confidence: float = 0.9) -> ForecastEnvelope:
    return ForecastEnvelope(
        target="capacity:cpu_saturation",
        prediction=0.7,
        lower=0.5,
        upper=0.9,
        probability=probability,
        confidence=confidence,
        model_version="ensemble:v1",
        evidence_ids=["ev-1", "ev-2"],
    )


def test_alias_points_at_the_same_engine() -> None:
    assert AdvisoryDecisionEngine is DecisionEngine


def test_low_confidence_abstains_and_requires_authorization() -> None:
    records = DecisionEngine().recommend(_envelope(probability=0.95, confidence=0.40))
    assert [r.action for r in records] == ["abstain"]
    assert records[0].requires_authorization is True
    assert records[0].rationale == "forecast confidence below decision threshold"


@pytest.mark.parametrize(
    ("probability", "expected_risk", "expected_actions", "needs_authorization"),
    [
        (0.60, ActionRisk.GOVERNED, {"scale_capacity", "prepare_traffic_shedding"}, True),
        (0.95, ActionRisk.GOVERNED, {"scale_capacity", "prepare_traffic_shedding"}, True),
        (0.30, ActionRisk.ADVISORY, {"prepare_warm_standby", "increase_monitoring"}, True),
        (0.59, ActionRisk.ADVISORY, {"prepare_warm_standby", "increase_monitoring"}, True),
        (0.29, ActionRisk.OBSERVE, {"continue_monitoring"}, False),
        (0.0, ActionRisk.OBSERVE, {"continue_monitoring"}, False),
    ],
)
def test_probability_bands_map_to_risk_and_actions(
    probability: float,
    expected_risk: ActionRisk,
    expected_actions: set[str],
    needs_authorization: bool,
) -> None:
    records = DecisionEngine().recommend(_envelope(probability=probability))
    assert {r.action for r in records} == expected_actions
    assert {r.risk for r in records} == {expected_risk}
    assert all(r.requires_authorization is needs_authorization for r in records)


def test_missing_probability_is_treated_as_zero_observation() -> None:
    records = DecisionEngine().recommend(_envelope(probability=None))
    assert {r.action for r in records} == {"continue_monitoring"}


def test_confidence_is_clipped_into_the_unit_interval() -> None:
    above = DecisionEngine().recommend(_envelope(probability=0.9, confidence=7.0))
    assert all(r.confidence == 1.0 for r in above)
    below = DecisionEngine().recommend(_envelope(probability=0.9, confidence=-3.0))
    assert [r.action for r in below] == ["abstain"]
    assert below[0].confidence == 0.0


def test_custom_policy_thresholds_are_honoured() -> None:
    engine = DecisionEngine(
        DecisionPolicy(
            governed_probability_threshold=0.9,
            advisory_probability_threshold=0.5,
            minimum_confidence=0.2,
        )
    )
    records = engine.recommend(_envelope(probability=0.7, confidence=0.3))
    assert {r.risk for r in records} == {ActionRisk.ADVISORY}


def test_records_carry_evidence_and_the_probability_in_their_rationale() -> None:
    records = DecisionEngine().recommend(_envelope(probability=0.612345))
    assert all(r.evidence_ids == ["ev-1", "ev-2"] for r in records)
    assert all("exceedance_probability=0.612" in r.rationale for r in records)
    assert all("model=ensemble:v1" in r.rationale for r in records)


def test_probability_and_interval_validators() -> None:
    assert DecisionEngine.validate_probability(None) is True
    assert DecisionEngine.validate_probability(0.0) is True
    assert DecisionEngine.validate_probability(1.0) is True
    assert DecisionEngine.validate_probability(1.01) is False
    assert DecisionEngine.validate_probability(-0.01) is False
    assert DecisionEngine.validate_interval(0.1, 0.5, 0.9) is True
    assert DecisionEngine.validate_interval(0.6, 0.5, 0.9) is False


class _Confidence:
    def __init__(self, value: str) -> None:
        self.value = value


class _ForecastLike:
    """Duck-typed stand-in for the core Forecast, as evaluate_forecast accepts."""

    target = "capacity:cpu_saturation"
    prediction = 0.8
    range_lower = 0.6
    range_upper = 0.95
    probability = 0.8
    model_version = "ensemble:v1"

    def __init__(self, confidence: str) -> None:
        self.confidence = _Confidence(confidence)
        self.evidence = ["ev-a"]


def test_evaluate_forecast_never_grants_authorization() -> None:
    result = DecisionEngine().evaluate_forecast(_ForecastLike("high"), impact_severity="critical")
    assert result.requires_human_authorization is True
    assert result.authorization_granted is False
    assert result.impact_severity == "critical"
    assert result.decision_class.value == "advisory"
    assert {a.risk for a in result.actions} == {ActionRisk.GOVERNED}


def test_evaluate_forecast_maps_confidence_labels_to_numbers() -> None:
    high = DecisionEngine().evaluate_forecast(_ForecastLike("high"))
    assert all(a.confidence == pytest.approx(0.85) for a in high.actions)
    medium = DecisionEngine().evaluate_forecast(_ForecastLike("medium"))
    assert all(a.confidence == pytest.approx(0.65) for a in medium.actions)
