"""ThresholdMonitor: Alert threshold crossings for active probabilistic forecasts."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from futuris.core.enums import ForecastEventType
from futuris.core.schemas import Forecast, ForecastEvent
from futuris.infra.events import EventEmitter, event_emitter
from futuris.storage.repositories import EventRepository


@dataclass
class AlertThreshold:
    """User-defined probability alert boundary."""

    threshold_id: UUID
    target: str
    probability_floor: float
    direction: Literal["above", "below"] = "above"


class ThresholdMonitor:
    """Monitors probability thresholds and emits de-duplicated forecast_threshold_crossed events."""

    def __init__(
        self,
        event_repo: EventRepository | None = None,
        emitter: EventEmitter | None = None,
    ) -> None:
        self.event_repo = event_repo
        self.emitter = emitter or event_emitter
        self.thresholds: list[AlertThreshold] = []
        self._fired_events: set[tuple[UUID, UUID]] = set()  # (forecast_id, threshold_id)

    def register_threshold(self, threshold: AlertThreshold) -> None:
        """Register a threshold check."""
        self.thresholds.append(threshold)

    async def evaluate_forecast(self, forecast: Forecast) -> list[ForecastEvent]:
        """Evaluate an active forecast against registered thresholds."""
        if forecast.probability is None:
            return []

        emitted: list[ForecastEvent] = []
        for thresh in self.thresholds:
            if thresh.target != forecast.target:
                continue

            dedup_key = (forecast.forecast_id, thresh.threshold_id)
            if dedup_key in self._fired_events:
                continue

            crossed = False
            prob = forecast.probability
            is_above = thresh.direction == "above" and prob >= thresh.probability_floor
            is_below = thresh.direction == "below" and prob <= thresh.probability_floor
            if is_above or is_below:
                crossed = True

            if crossed:
                event = ForecastEvent(
                    event_id=uuid4(),
                    forecast_id=forecast.forecast_id,
                    event_type=ForecastEventType.FORECAST_THRESHOLD_CROSSED,
                    payload={
                        "target": forecast.target,
                        "probability": forecast.probability,
                        "probability_floor": thresh.probability_floor,
                        "direction": thresh.direction,
                        "forecast": forecast.model_dump(mode="json"),
                    },
                    emitted_at=datetime.now(UTC),
                )
                self._fired_events.add(dedup_key)
                emitted.append(event)

                if self.event_repo:
                    await self.event_repo.append(event)
                await self.emitter.emit(event)

        return emitted
