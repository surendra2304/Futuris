"""Forge build predictor estimating duration, success probabilities, token cost, and capacity."""

from dataclasses import dataclass

import numpy as np


@dataclass
class TaskComplexityProfile:
    file_count: int
    lines_of_code: int
    dependency_count: int
    has_custom_mcp: bool = False
    has_frontend_build: bool = False


@dataclass
class BuildPredictionResult:
    predicted_duration_seconds: float
    duration_lower_bound: float
    duration_upper_bound: float
    first_attempt_success_probability: float
    estimated_ai_universe_tokens: int
    recommended_template: str
    capacity_exhaustion_risk: float
    recommended_action: str  # "PROCEED_IMMEDIATELY" | "QUEUE_TASK" | "REDUCE_SCOPE"


class ForgeBuildPredictor:
    """Predicts build duration, first-attempt pass rates, token costs, and template fit."""

    def __init__(self, token_cost_per_million: float = 3.0) -> None:
        self.token_cost_per_million = token_cost_per_million

    def predict_build_characteristics(
        self,
        task: TaskComplexityProfile,
        template_history: dict[str, list[bool]] | None = None,
        concurrent_tasks: int = 4,
        max_concurrent_limit: int = 10,
    ) -> BuildPredictionResult:
        """Predict execution characteristics for a newly submitted task."""
        # 1. Base duration calculation from task complexity
        base_sec = (
            30.0
            + (task.file_count * 4.5)
            + (task.lines_of_code * 0.05)
            + (task.dependency_count * 2.0)
        )
        if task.has_custom_mcp:
            base_sec += 45.0
        if task.has_frontend_build:
            base_sec += 35.0

        lower_bound = max(10.0, base_sec * 0.8)
        upper_bound = base_sec * 1.35

        # 2. First-attempt success probability
        complexity_penalty = min(
            0.40,
            (task.file_count * 0.015) + (task.dependency_count * 0.01),
        )
        baseline_success = 0.92 - complexity_penalty
        if task.has_custom_mcp:
            baseline_success -= 0.05
        success_prob = float(np.clip(baseline_success, 0.20, 0.95))

        # 3. Estimated AI-Universe Token Volume
        est_tokens = int(25000 + (task.file_count * 3500) + (task.lines_of_code * 12))
        if task.has_frontend_build:
            est_tokens += 15000

        # 4. Best Template Selection
        best_template = "fastapi_react_fullstack_v2"
        if template_history:
            best_rate = -1.0
            for t_name, hist in template_history.items():
                if hist:
                    rate = sum(hist) / len(hist)
                    if rate > best_rate:
                        best_rate = rate
                        best_template = t_name

        # 5. Capacity Exhaustion Risk & Resource Allocation
        utilization = concurrent_tasks / max_concurrent_limit
        exhaustion_risk = float(np.clip(utilization**2, 0.0, 1.0))

        if exhaustion_risk > 0.75:
            rec_action = "QUEUE_TASK"
        elif success_prob < 0.40:
            rec_action = "REDUCE_SCOPE"
        else:
            rec_action = "PROCEED_IMMEDIATELY"

        return BuildPredictionResult(
            predicted_duration_seconds=round(base_sec, 1),
            duration_lower_bound=round(lower_bound, 1),
            duration_upper_bound=round(upper_bound, 1),
            first_attempt_success_probability=round(success_prob, 3),
            estimated_ai_universe_tokens=est_tokens,
            recommended_template=best_template,
            capacity_exhaustion_risk=round(exhaustion_risk, 3),
            recommended_action=rec_action,
        )
