"""ConfidenceAssessor: Evaluates meta-confidence regarding model calibration quality."""

from dataclasses import dataclass

from futuris.core.enums import ConfidenceLevel
from futuris.features.normalize import DataQualityReport


@dataclass
class ConfidenceAssessmentResult:
    """Detailed score breakdown and resulting ConfidenceLevel."""

    level: ConfidenceLevel
    reasons: list[str]
    historical_count: int
    recent_degradation_detected: bool
    data_quality_score: float


class ConfidenceAssessor:
    """Computes meta-confidence level (HIGH / MEDIUM / LOW) based on four explicit rules:

    Rule 1 (Data Quality): High data gaps or low coverage (< 85%) -> LOW.
    Rule 2 (Historical Samples): < 10 -> LOW; 10 to 49 -> MEDIUM; >= 50 -> HIGH candidate.
    Rule 3 (Backtest Sample Size): Model backtest sample size < 20 -> max MEDIUM.
    Rule 4 (Recent Degradation): If recent 30-day MAE is > 20% worse than long-run -> degrade.
    """

    def evaluate(
        self,
        historical_resolved_count: int,
        backtest_sample_size: int,
        long_run_mae: float,
        recent_30d_mae: float,
        quality_report: DataQualityReport | None = None,
    ) -> ConfidenceAssessmentResult:
        reasons: list[str] = []
        recent_degradation = False

        # Rule 1: Check Data Quality Report
        if quality_report and quality_report.coverage_percentage < 85.0:
            reasons.append(f"Low signal coverage ({quality_report.coverage_percentage}%)")
            return ConfidenceAssessmentResult(
                level=ConfidenceLevel.LOW,
                reasons=reasons,
                historical_count=historical_resolved_count,
                recent_degradation_detected=False,
                data_quality_score=quality_report.coverage_percentage,
            )

        # Rule 2: Baseline level from historical sample size
        if historical_resolved_count < 10:
            level = ConfidenceLevel.LOW
            reasons.append(f"Sparse historical target samples (n={historical_resolved_count} < 10)")
        elif historical_resolved_count < 50:
            level = ConfidenceLevel.MEDIUM
            reasons.append(f"Moderate historical sample depth (n={historical_resolved_count})")
        else:
            level = ConfidenceLevel.HIGH
            reasons.append(f"Rich historical sample depth (n={historical_resolved_count})")

        # Rule 3: Backtest sample size constraint
        if backtest_sample_size < 20 and level == ConfidenceLevel.HIGH:
            level = ConfidenceLevel.MEDIUM
            reasons.append(f"Limited backtest validation sample (n={backtest_sample_size} < 20)")

        # Rule 4: Recent degradation check (>20% degradation vs long-run)
        if long_run_mae > 0 and recent_30d_mae > 1.20 * long_run_mae:
            recent_degradation = True
            reasons.append(
                f"Model error degraded >20% recently ({recent_30d_mae:.2f} vs {long_run_mae:.2f})"
            )
            # Degrade one level
            if level == ConfidenceLevel.HIGH:
                level = ConfidenceLevel.MEDIUM
            elif level == ConfidenceLevel.MEDIUM:
                level = ConfidenceLevel.LOW

        coverage = quality_report.coverage_percentage if quality_report else 100.0
        return ConfidenceAssessmentResult(
            level=level,
            reasons=reasons,
            historical_count=historical_resolved_count,
            recent_degradation_detected=recent_degradation,
            data_quality_score=coverage,
        )


confidence_assessor = ConfidenceAssessor()
