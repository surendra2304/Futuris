from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Iterable

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

    def require(self, forecast: ForecastEnvelope) -> None:
        report = self.evaluate(forecast)
        if not report.passed:
            details = "; ".join(issue.message for issue in report.errors)
            raise ValueError(details)


def coverage_score(required: Iterable[str], observed: Iterable[str]) -> float:
    required_set = set(required)
    if not required_set:
        return 1.0
    observed_set = set(observed)
    return len(required_set & observed_set) / len(required_set)
