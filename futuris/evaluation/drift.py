"""DriftMonitor: Rolling-origin score monitoring and statistical control limits."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import numpy as np

from futuris.core.enums import ForecastEventType
from futuris.core.schemas import ForecastEvent
from futuris.storage.repositories import EventRepository


@dataclass
class DriftStatus:
    """Drift evaluation result."""

    model_version: str
    is_degraded: bool
    control_limit: float
    current_score: float
    metric_name: str
    message: str


class DriftMonitor:
    """Monitors rolling performance and emits model_degraded events when crossing control limits."""

    def __init__(self, event_repo: EventRepository | None = None) -> None:
        self.event_repo = event_repo

    def evaluate_drift(
        self,
        model_version: str,
        historical_scores: list[float],
        recent_scores: list[float],
        metric_name: str = "mae",
        sigma_threshold: float = 3.0,
    ) -> DriftStatus:
        """Evaluate if recent rolling scores exceed historical statistical control limits."""
        if len(historical_scores) < 5 or len(recent_scores) == 0:
            return DriftStatus(
                model_version=model_version,
                is_degraded=False,
                control_limit=0.0,
                current_score=float(np.mean(recent_scores)) if recent_scores else 0.0,
                metric_name=metric_name,
                message="Insufficient score history for drift control evaluation",
            )

        hist_arr = np.asarray(historical_scores, dtype=float)
        rec_arr = np.asarray(recent_scores, dtype=float)

        mean_hist = float(np.mean(hist_arr))
        std_hist = float(np.std(hist_arr))
        control_limit = mean_hist + (sigma_threshold * std_hist)
        current_score = float(np.mean(rec_arr))

        is_degraded = current_score > control_limit
        if is_degraded:
            msg = (
                f"Model {model_version} degradation: {metric_name} ({current_score:.4f}) > "
                f"control limit ({control_limit:.4f})"
            )
        else:
            msg = (
                f"Model {model_version} within control limits "
                f"({current_score:.4f} <= {control_limit:.4f})"
            )

        return DriftStatus(
            model_version=model_version,
            is_degraded=is_degraded,
            control_limit=round(control_limit, 4),
            current_score=round(current_score, 4),
            metric_name=metric_name,
            message=msg,
        )

    async def check_and_emit(
        self,
        model_version: str,
        historical_scores: list[float],
        recent_scores: list[float],
        metric_name: str = "mae",
    ) -> DriftStatus:
        """Evaluate drift and emit model_degraded event if degraded."""
        status = self.evaluate_drift(model_version, historical_scores, recent_scores, metric_name)

        if status.is_degraded and self.event_repo:
            event = ForecastEvent(
                event_id=uuid4(),
                forecast_id=None,
                event_type=ForecastEventType.MODEL_DEGRADED,
                payload={
                    "model_version": model_version,
                    "metric": metric_name,
                    "current_score": status.current_score,
                    "control_limit": status.control_limit,
                    "reason": status.message,
                },
                emitted_at=datetime.now(UTC),
            )
            await self.event_repo.append(event)

        return status
