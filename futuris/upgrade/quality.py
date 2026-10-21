from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from math import isfinite
from typing import Any

from .models import ForecastEnvelope


@dataclass(frozen=True)
class QualityIssue:
    code: str
    message: str
    severity: str = "error"


@dataclass
class QualityReport:
    passed: bool
    issues: list[QualityIssue] = field(default_factory=list)

    def add(self, code: str, message: str, severity: str = "error") -> None:
        self.issues.append(QualityIssue(code, message, severity))

    @property
    def errors(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.severity == "error"]


class ForecastQualityGate:
    def evaluate(self, forecast: ForecastEnvelope) -> QualityReport:
        report = QualityReport(True)
        nums = [forecast.prediction, forecast.lower, forecast.upper, forecast.confidence]
        if not all(isfinite(v) for v in nums):
            report.add("non_finite", "forecast contains NaN or infinity")
        if not 0.0 <= forecast.confidence <= 1.0:
            report.add("confidence_range", "confidence must be between 0 and 1")
        if forecast.probability is not None and not 0.0 <= forecast.probability <= 1.0:
            report.add("probability_range", "probability must be between 0 and 1")
        if not forecast.lower <= forecast.prediction <= forecast.upper:
            report.add("interval_order", "lower <= prediction <= upper invariant failed")
        if not forecast.target.strip():
            report.add("target_missing", "forecast target is empty")
        if not forecast.model_version.strip():
            report.add("model_missing", "model version is empty")
        if not forecast.evidence_ids:
            report.add("evidence_missing", "forecast has no evidence reference")
        report.passed = not report.errors
        return report

    @classmethod
    def require(cls, forecast: Any) -> None:
        passed, errors = cls.validate_forecast(forecast)
        if not passed:
            details = "; ".join(errors)
            raise ValueError(details)

    @classmethod
    def validate_forecast(cls, forecast: Any) -> tuple[bool, list[str]]:
        """Validate supported forecast schemas without manufacturing missing fields.

        `ForecastEnvelope` is the native upgrade contract. The core domain
        `Forecast` is also supported for the application pipeline; its
        categorical confidence maps to the gate's numeric calibration scale.
        Arbitrary or partially populated objects are rejected explicitly
        instead of having zero-valued predictions, intervals, and confidence
        synthesized around them.
        """
        gate = cls()
        if isinstance(forecast, ForecastEnvelope):
            env = forecast
        else:
            # Import lazily to keep the upgrade package importable on its own
            # and avoid a module-level dependency between the two domain layers.
            from futuris.core.enums import ConfidenceLevel
            from futuris.core.schemas import Forecast as CoreForecast

            if not isinstance(forecast, CoreForecast):
                return False, [
                    "unsupported forecast type; expected ForecastEnvelope or core Forecast"
                ]

            confidence_map = {
                ConfidenceLevel.HIGH: 0.85,
                ConfidenceLevel.MEDIUM: 0.65,
                ConfidenceLevel.LOW: 0.45,
            }
            env = ForecastEnvelope(
                forecast_id=forecast.forecast_id,
                target=forecast.target,
                prediction=forecast.prediction,
                lower=forecast.range_lower,
                upper=forecast.range_upper,
                probability=forecast.probability,
                confidence=confidence_map[forecast.confidence],
                model_version=forecast.model_version,
                evidence_ids=[str(evidence.evidence_id) for evidence in forecast.evidence],
            )

        rep = gate.evaluate(env)
        return rep.passed, [issue.message for issue in rep.errors]


def coverage_score(required: Iterable[str], observed: Iterable[str]) -> float:
    required_set = set(required)
    if not required_set:
        return 1.0
    observed_set = set(observed)
    return len(required_set & observed_set) / len(required_set)
