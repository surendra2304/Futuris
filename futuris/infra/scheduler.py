"""Continuous autonomous forecasting scheduler with APScheduler and noise suppression."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pandas as pd
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from futuris.agents.runner import AgentRunner
from futuris.connectors.synthetic_telemetry import SyntheticTelemetryConnector
from futuris.core.enums import ForecastEventType, ForecastStatus
from futuris.core.lifecycle import LifecycleManager
from futuris.core.pipeline import ForecastingPipeline
from futuris.core.schemas import Forecast, ForecastEvent
from futuris.evaluation.backtest import BacktestEngine
from futuris.evaluation.drift import DriftMonitor
from futuris.infra.events import EventEmitter, event_emitter
from futuris.infra.logging import get_logger
from futuris.storage.repositories import (
    EventRepository,
    ForecastRepository,
    OutcomeRepository,
)

from futuris.upgrade.scheduler import DistributedLeaseTable, SafeScheduler, ScheduleSpec

logger = get_logger("futuris.scheduler")


@dataclass
class ForecastSubscription:
    """Subscription configuration defining scheduled forecast refresh requirements."""

    target: str
    horizon: timedelta = timedelta(hours=24)
    refresh_interval_minutes: int = 60
    delta_prob_threshold: float = 0.05
    delta_pred_threshold: float = 50.0
    enabled: bool = True


class ForecastScheduler:
    """Orchestrates unattended ingestion, forecast refreshes, lifecycle sweeps, and agent runs."""

    def __init__(
        self,
        forecast_repo: ForecastRepository | None = None,
        outcome_repo: OutcomeRepository | None = None,
        event_repo: EventRepository | None = None,
        emitter: EventEmitter | None = None,
        pipeline: ForecastingPipeline | None = None,
        agent_runner: AgentRunner | None = None,
        lifecycle_manager: LifecycleManager | None = None,
        lease_table: DistributedLeaseTable | None = None,
    ) -> None:
        self.scheduler = AsyncIOScheduler()
        self.forecast_repo = forecast_repo
        self.outcome_repo = outcome_repo
        self.event_repo = event_repo
        self.emitter = emitter or event_emitter
        self.pipeline = pipeline or ForecastingPipeline()
        self.agent_runner = agent_runner or AgentRunner()
        self.lifecycle_manager = lifecycle_manager
        self.drift_monitor = DriftMonitor(event_repo=event_repo)
        self.safe_scheduler = SafeScheduler(lease_table)
        self.subscriptions: list[ForecastSubscription] = [
            ForecastSubscription(target="service:checkout:capacity_exceedance_24h")
        ]
        self._in_flight_tasks: set[asyncio.Task] = set()

    def should_suppress_refresh_event(
        self,
        previous_forecast: Forecast,
        new_forecast: Forecast,
        delta_prob_threshold: float = 0.05,
        delta_pred_threshold: float = 50.0,
    ) -> bool:
        """Return True if change is below significance delta to suppress notification noise."""
        prev_p = previous_forecast.probability or 0.0
        new_p = new_forecast.probability or 0.0
        prob_diff = abs(new_p - prev_p)
        pred_diff = abs(new_forecast.prediction - previous_forecast.prediction)

        return prob_diff < delta_prob_threshold and pred_diff < delta_pred_threshold

    async def ingest_job(self) -> int:
        """Scheduled ingestion job pulling fresh telemetry."""
        logger.info("ingest_job_started")
        connector = SyntheticTelemetryConnector(seed=42)
        now = datetime.now(UTC)
        obs = await connector.fetch(now - timedelta(days=1), now)
        logger.info("ingest_job_completed", points=len(obs))
        return len(obs)

    async def forecast_refresh_job(self) -> list[Forecast]:
        """Scheduled job orchestrating forecasts and emitting updates for meaningful movements."""
        logger.info("forecast_refresh_job_started")
        refreshed: list[Forecast] = []
        now = datetime.now(UTC)

        for sub in self.subscriptions:
            if not sub.enabled:
                continue

            prev_forecast: Forecast | None = None
            if self.forecast_repo:
                existing = await self.forecast_repo.list_by_target(sub.target)
                active_list = [f for f in existing if f.status == ForecastStatus.ACTIVE]
                if active_list:
                    prev_forecast = active_list[-1]

            res = await self.pipeline.run(target=sub.target, as_of=now, horizon=sub.horizon)
            new_f = res.forecast

            if self.forecast_repo:
                await self.forecast_repo.create(new_f)

            refreshed.append(new_f)

            if prev_forecast:
                suppress = self.should_suppress_refresh_event(
                    previous_forecast=prev_forecast,
                    new_forecast=new_f,
                    delta_prob_threshold=sub.delta_prob_threshold,
                    delta_pred_threshold=sub.delta_pred_threshold,
                )
                if not suppress:
                    event = ForecastEvent(
                        event_id=uuid4(),
                        forecast_id=new_f.forecast_id,
                        event_type=ForecastEventType.FORECAST_UPDATED,
                        payload={
                            "target": new_f.target,
                            "previous_probability": prev_forecast.probability,
                            "new_probability": new_f.probability,
                            "previous_prediction": prev_forecast.prediction,
                            "new_prediction": new_f.prediction,
                        },
                        emitted_at=now,
                    )
                    if self.event_repo:
                        await self.event_repo.append(event)
                    await self.emitter.emit(event)

        return refreshed

    async def lifecycle_sweep_job(self) -> Any:
        """Scheduled lifecycle resolution and invalidation sweep."""
        logger.info("lifecycle_sweep_job_started")
        if self.lifecycle_manager:
            now = datetime.now(UTC)
            connector = SyntheticTelemetryConnector(seed=42)
            obs = await connector.fetch(now - timedelta(days=2), now)
            df = pd.DataFrame([{"timestamp": o.observed_at, "value": o.value} for o in obs])
            return await self.lifecycle_manager.run_lifecycle_sweep(
                observations_df=df, as_of=now
            )
        return None

    async def backtest_nightly_job(self) -> None:
        """Nightly backtesting and drift check job."""
        logger.info("backtest_nightly_job_started")
        backtester = BacktestEngine(
            forecast_repo=self.forecast_repo,
            outcome_repo=self.outcome_repo,
        )
        now = datetime.now(UTC)
        for sub in self.subscriptions:
            report = await backtester.run_backtest(
                target=sub.target,
                start_date=now - timedelta(days=7),
                end_date=now,
                stride_hours=24,
                horizon=sub.horizon,
            )
            h_metrics = list(report.metrics_by_horizon.values())
            if h_metrics:
                recent_mae = h_metrics[0].mae
                hist_maes = [40.0, 42.0, 39.5, 41.0, 40.5]
                await self.drift_monitor.check_and_emit(
                    model_version="auto_arima@v1",
                    historical_scores=hist_maes,
                    recent_scores=[recent_mae],
                )

    def start(self) -> None:
        """Start scheduler with registered recurring jobs."""
        self.scheduler.add_job(self.ingest_job, "interval", minutes=15, id="ingest_job")
        self.scheduler.add_job(
            self.forecast_refresh_job, "interval", minutes=60, id="forecast_refresh_job"
        )
        self.scheduler.add_job(
            self.lifecycle_sweep_job, "interval", minutes=30, id="lifecycle_sweep_job"
        )
        self.scheduler.add_job(
            self.backtest_nightly_job, "cron", hour=2, id="backtest_nightly_job"
        )
        self.scheduler.start()
        logger.info("scheduler_started")

    async def shutdown(self) -> None:
        """Gracefully drain running jobs and shutdown scheduler."""
        logger.info("scheduler_shutdown_requested")
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
        if self._in_flight_tasks:
            await asyncio.gather(*self._in_flight_tasks, return_exceptions=True)
        logger.info("scheduler_shutdown_complete")
