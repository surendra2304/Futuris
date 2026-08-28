"""LifecycleManager: State transitions, assumption invalidation, expiry, and outcome resolution."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pandas as pd

from futuris.core.enums import ForecastEventType, ForecastStatus
from futuris.core.resolution import OutcomeResolver, outcome_resolver
from futuris.core.schemas import Forecast, ForecastEvent, Outcome
from futuris.infra.events import EventEmitter, event_emitter
from futuris.storage.repositories import (
    EventRepository,
    ForecastRepository,
    OutcomeRepository,
)


@dataclass
class LifecycleSweepReport:
    """Summary metrics of a lifecycle sweep execution."""

    expired_count: int
    resolved_count: int
    invalidated_count: int
    reassessment_due_count: int
    outcomes: list[Outcome]


def _ensure_utc(dt: datetime) -> datetime:
    """Ensure datetime has UTC timezone."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


class LifecycleManager:
    """Orchestrates continuous forecast lifecycle transitions: expiry, resolution, invalidation."""

    def __init__(
        self,
        forecast_repo: ForecastRepository,
        outcome_repo: OutcomeRepository,
        event_repo: EventRepository,
        resolver: OutcomeResolver | None = None,
        emitter: EventEmitter | None = None,
    ) -> None:
        self.forecast_repo = forecast_repo
        self.outcome_repo = outcome_repo
        self.event_repo = event_repo
        self.resolver = resolver or outcome_resolver
        self.emitter = emitter or event_emitter

    async def list_due_for_review(self, as_of: datetime | None = None) -> list[Forecast]:
        """List active forecasts due for reassessment (review_at <= as_of)."""
        now = _ensure_utc(as_of or datetime.now(UTC))
        active = await self.forecast_repo.list_by_status(ForecastStatus.ACTIVE)
        return [f for f in active if _ensure_utc(f.review_at) <= now]

    async def invalidate_forecast(self, forecast: Forecast, reason: str) -> Forecast:
        """Invalidate an active forecast whose assumptions have broken."""
        updated = await self.forecast_repo.update_status(
            forecast.forecast_id, ForecastStatus.INVALIDATED
        )
        event = ForecastEvent(
            event_id=uuid4(),
            forecast_id=forecast.forecast_id,
            event_type=ForecastEventType.FORECAST_INVALIDATED,
            payload={"reason": reason, "target": forecast.target},
            emitted_at=datetime.now(UTC),
        )
        await self.event_repo.append(event)
        await self.emitter.emit(event)
        return updated or forecast

    async def run_lifecycle_sweep(
        self,
        observations_df: pd.DataFrame,
        capacity_events: list[dict] | None = None,
        as_of: datetime | None = None,
    ) -> LifecycleSweepReport:
        """Execute complete state transition sweep across all active forecasts."""
        now = _ensure_utc(as_of or datetime.now(UTC))
        active_forecasts = await self.forecast_repo.list_by_status(ForecastStatus.ACTIVE)

        expired_count = 0
        resolved_count = 0
        invalidated_count = 0
        recorded_outcomes: list[Outcome] = []

        for forecast in active_forecasts:
            f_as_of = _ensure_utc(forecast.as_of)
            f_expires = _ensure_utc(forecast.expires_at)

            # 1. Check Assumption Breaks (e.g. Capacity change event occurred in window)
            if capacity_events:
                broken = False
                for ce in capacity_events:
                    raw_ts = ce.get("timestamp")
                    if raw_ts:
                        ts = _ensure_utc(raw_ts)
                        if f_as_of <= ts <= f_expires:
                            new_cap = ce.get("new_capacity")
                            await self.invalidate_forecast(
                                forecast,
                                f"Assumption broken: capacity changed to {new_cap} at {ts}",
                            )
                            invalidated_count += 1
                            broken = True
                            break
                if broken:
                    continue

            # 2. Check Horizon Resolution (now >= expires_at)
            if now >= f_expires:
                outcome = self.resolver.resolve_forecast(forecast, observations_df)
                saved_outcome = await self.outcome_repo.record_outcome(outcome)
                recorded_outcomes.append(saved_outcome)

                # Emit outcome event
                event = ForecastEvent(
                    event_id=uuid4(),
                    forecast_id=forecast.forecast_id,
                    event_type=ForecastEventType.FORECAST_OUTCOME_RECORDED,
                    payload=saved_outcome.model_dump(mode="json"),
                    emitted_at=datetime.now(UTC),
                )
                await self.event_repo.append(event)
                await self.emitter.emit(event)
                resolved_count += 1
                continue

            # 3. Check Expiry
            if now > f_expires:
                await self.forecast_repo.update_status(forecast.forecast_id, ForecastStatus.EXPIRED)
                expired_count += 1

        review_due = await self.list_due_for_review(now)

        return LifecycleSweepReport(
            expired_count=expired_count,
            resolved_count=resolved_count,
            invalidated_count=invalidated_count,
            reassessment_due_count=len(review_due),
            outcomes=recorded_outcomes,
        )
