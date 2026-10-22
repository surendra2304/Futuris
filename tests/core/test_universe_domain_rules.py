"""Target resolution and risk classification in the universe registry.

Covers strict versus permissive target resolution, the non-strict display fallback
(used by internal callers only; the public surface resolves strictly), and risk
classification in both directions: higher-is-worse and lower-is-worse.
"""

from __future__ import annotations

import pytest

from futuris.core.universe_domains import (
    UNIVERSE_TARGETS,
    DomainTargetSpec,
    RiskLevel,
    UniverseDomain,
    evaluate_risk_level,
    get_target_spec,
)


def _higher_is_worse(
    *, high: float = 5.0, critical: float = 10.0, unit: str = "idx"
) -> DomainTargetSpec:
    """Risk rises with the value, so critical is the larger threshold."""
    return DomainTargetSpec(
        target="test:higher:target",
        name="Higher",
        domain=UniverseDomain.INFRA,
        unit=unit,
        description="test fixture",
        high_risk_threshold=high,
        critical_risk_threshold=critical,
        is_lower_better=True,
    )


def _lower_is_worse() -> DomainTargetSpec:
    """Risk rises as the value falls, so critical is the smaller threshold."""
    return DomainTargetSpec(
        target="test:lower:target",
        name="Lower",
        domain=UniverseDomain.INFRA,
        unit="idx",
        description="test fixture",
        high_risk_threshold=10.0,
        critical_risk_threshold=5.0,
        is_lower_better=False,
    )


def test_strict_lookup_refuses_unregistered_targets() -> None:
    registered = next(iter(UNIVERSE_TARGETS))
    assert get_target_spec(registered, strict=True) is UNIVERSE_TARGETS[registered]
    with pytest.raises(KeyError):
        get_target_spec("not:a:registered:target", strict=True)


def test_default_lookup_is_permissive_which_is_why_callers_must_pass_strict() -> None:
    """Documents the default: an unregistered name gets a spec built from its prefix."""
    spec = get_target_spec("not:a:registered:target")
    assert spec.target == "not:a:registered:target"
    assert spec.pipeline_target is False


@pytest.mark.parametrize(
    ("target", "domain", "unit"),
    [
        ("sentinel:anything", UniverseDomain.SENTINEL, "%"),
        ("cortex:queue", UniverseDomain.CORTEX, "%"),
        ("forge:build_failures", UniverseDomain.FORGE, "%"),
        ("memora:exhaustion_days", UniverseDomain.MEMORA, "days"),
        ("memora:latency", UniverseDomain.MEMORA, "ms"),
        ("inference:error_rate", UniverseDomain.INFERENCE, "%"),
        ("intelx:signal", UniverseDomain.INTELX, "idx"),
        ("market:volatility", UniverseDomain.STRATEX, "%"),
        ("btc_spot_flow", UniverseDomain.STRATEX, "%"),
        ("friday:unlisted", UniverseDomain.FRIDAY, "%"),
        ("edge:rpm_capacity", UniverseDomain.INFRA, "rpm"),
        ("unknown:thing", UniverseDomain.INFRA, "idx"),
    ],
)
def test_non_strict_fallback_deduces_domain_and_unit_from_the_prefix(
    target: str, domain: UniverseDomain, unit: str
) -> None:
    spec = get_target_spec(target, strict=False)
    assert spec.target == target
    assert spec.domain == domain
    assert spec.unit == unit
    assert spec.pipeline_target is False


def test_non_strict_fallback_builds_a_readable_name() -> None:
    assert get_target_spec("sentinel:queue_depth", strict=False).name == "Sentinel Queue Depth"


def test_higher_is_worse_classification_uses_the_thresholds() -> None:
    spec = _higher_is_worse()
    assert evaluate_risk_level(spec, 12.0) == RiskLevel.CRITICAL
    assert evaluate_risk_level(spec, 6.0) == RiskLevel.HIGH
    assert evaluate_risk_level(spec, 3.5) == RiskLevel.ELEVATED  # 5 * 0.6 = 3.0
    assert evaluate_risk_level(spec, 2.0) == RiskLevel.NOMINAL


def test_lower_is_worse_classification_uses_the_thresholds() -> None:
    spec = _lower_is_worse()
    assert evaluate_risk_level(spec, 4.0) == RiskLevel.CRITICAL
    assert evaluate_risk_level(spec, 9.0) == RiskLevel.HIGH
    assert evaluate_risk_level(spec, 12.0) == RiskLevel.ELEVATED  # 10 * 1.3 = 13
    assert evaluate_risk_level(spec, 20.0) == RiskLevel.NOMINAL


def test_percent_targets_use_the_probability_when_one_is_supplied() -> None:
    spec = _higher_is_worse(high=0.6, critical=0.7, unit="%")
    assert evaluate_risk_level(spec, prediction=1.0, probability=0.8) == RiskLevel.CRITICAL


def test_percent_predictions_on_the_0_100_scale_are_normalised_before_comparison() -> None:
    """Regression: 42.0 compared raw against a 0.60 threshold used to read CRITICAL."""
    spec = _higher_is_worse(high=0.6, critical=0.7, unit="%")
    assert evaluate_risk_level(spec, prediction=42.0, probability=None) == RiskLevel.ELEVATED
    assert evaluate_risk_level(spec, prediction=0.8, probability=None) == RiskLevel.CRITICAL
