"""Continuous autonomous forecasting scheduler with APScheduler and noise suppression."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pandas as pd
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from futuris.agents.runner import AgentRunner
from futuris.connectors.factory import build_scheduler_connector
from futuris.core.enums import ForecastEventType, ForecastStatus
from futuris.core.lifecycle import LifecycleManager
from futuris.core.pipeline import ForecastingPipeline, IngestionStage
from futuris.core.schemas import Forecast, ForecastEvent
from futuris.evaluation.backtest import BacktestEngine
from futuris.evaluation.drift import DriftMonitor
from futuris.infra.events import EventEmitter, event_emitter
from futuris.infra.logging import get_logger
from futuris.storage.repositories import (
    EvaluationRepository,
    EventRepository,
    ForecastRepository,
    ObservationRepository,
    OutcomeRepository,
)
from futuris.upgrade.scheduler import DistributedLeaseTable, SafeScheduler

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
        observation_repo: ObservationRepository | None = None,
        evaluation_repo: EvaluationRepository | None = None,
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
        self.observation_repo = observation_repo
        self.evaluation_repo = evaluation_repo
        self.emitter = emitter or event_emitter
        # The unattended pipeline reads from the configured telemetry source
        # (FUTURIS_TELEMETRY_SOURCE), not a hardcoded synthetic generator.
        self.pipeline = pipeline or ForecastingPipeline(
            ingestion=IngestionStage(connector=build_scheduler_connector())
        )
        self.agent_runner = agent_runner or AgentRunner()
        self.lifecycle_manager = lifecycle_manager
        self.drift_monitor = DriftMonitor(event_repo=event_repo)
        self.safe_scheduler = SafeScheduler(lease_table)
        self.subscriptions: list[ForecastSubscription] = [
            ForecastSubscription(target="service:checkout:capacity_exceedance_24h"),
            ForecastSubscription(target="trading:BTCUSDT:price_variance_24h"),
            ForecastSubscription(target="trading:ETHUSDT:price_variance_24h"),
            ForecastSubscription(target="trading:SOLUSDT:price_variance_24h"),
            ForecastSubscription(target="macro:SPY:return_exceedance_24h"),
            ForecastSubscription(target="macro:DGS10:yield_shift_30d"),
            ForecastSubscription(target="macro:T10Y2Y:curve_inversion_30d"),
            ForecastSubscription(target="macro:UNRATE:unemployment_drift_90d"),
        ]
        self._in_flight_tasks: set[asyncio.Task] = set()
        self._mae_history: dict[str, list[float]] = {}

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

    async def sync_dynamic_trading_subscriptions(self) -> list[str]:
        """Query Stratex to discover any newly active trading pairs and auto-register subscriptions."""
        from futuris.connectors.trading_bot import TradingBotConnector

        discovered_targets: list[str] = []
        try:
            connector = TradingBotConnector()
            pairs = await connector.discover_active_trade_pairs()
            existing_targets = {sub.target for sub in self.subscriptions}

            for pair in pairs:
                target = f"trading:{pair}:price_variance_24h"
                discovered_targets.append(target)
                if target not in existing_targets:
                    self.subscriptions.append(
                        ForecastSubscription(
                            target=target,
                            horizon=timedelta(hours=24),
                            refresh_interval_minutes=60,
                            delta_prob_threshold=0.05,
                            delta_pred_threshold=25.0,
                            enabled=True,
                        )
                    )
                    logger.info("dynamic_trading_subscription_registered", target=target, pair=pair)
        except Exception as exc:
            logger.debug("dynamic_trading_subscription_sync_failed", error=str(exc))

        return discovered_targets

    async def ingest_job(self) -> int:
        """Scheduled ingestion job pulling fresh telemetry and persisting observations."""
        logger.info("ingest_job_started")
        await self.sync_dynamic_trading_subscriptions()
        connector = build_scheduler_connector()
        now = datetime.now(UTC)
        obs = await connector.fetch(now - timedelta(days=1), now)
        if self.observation_repo and obs:
            await self.observation_repo.record_batch(obs)
        logger.info("ingest_job_completed", points=len(obs))
        return len(obs)

    async def forecast_refresh_job(self) -> list[Forecast]:
        """Scheduled job orchestrating forecasts and emitting updates for meaningful movements."""
        logger.info("forecast_refresh_job_started")
        await self.sync_dynamic_trading_subscriptions()
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

            # High-conviction or significant forecast broadcast to FRIDAY mesh
            if new_f.probability and new_f.probability >= 0.70:
                try:
                    from futuris.ecosystem.signals import signal_dispatcher

                    t = asyncio.create_task(
                        signal_dispatcher.notify_friday_forecast(
                            {
                                "forecast_id": str(new_f.forecast_id),
                                "target": new_f.target,
                                "prediction": round(float(new_f.prediction), 4),
                                "probability": round(float(new_f.probability), 4),
                                "range_lower": round(float(new_f.range_lower), 4),
                                "range_upper": round(float(new_f.range_upper), 4),
                                "model_version": new_f.model_version,
                                "status": new_f.status.value,
                            }
                        )
                    )
                    self._in_flight_tasks.add(t)
                    t.add_done_callback(self._in_flight_tasks.discard)
                except Exception as exc:
                    logger.debug("friday_forecast_dispatch_skipped", error=str(exc))

        return refreshed

    async def lifecycle_sweep_job(self) -> Any:
        """Scheduled lifecycle resolution and invalidation sweep."""
        logger.info("lifecycle_sweep_job_started")
        if self.lifecycle_manager:
            now = datetime.now(UTC)
            connector = build_scheduler_connector()
            obs = await connector.fetch(now - timedelta(days=2), now)
            df = pd.DataFrame([{"timestamp": o.observed_at, "value": o.value} for o in obs])
            return await self.lifecycle_manager.run_lifecycle_sweep(observations_df=df, as_of=now)
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
                # Drift is only detectable against real history: keep the MAEs
                # this process has actually measured and wait for enough of
                # them instead of comparing against invented baselines.
                history = self._mae_history.setdefault(sub.target, [])
                history.append(recent_mae)
                window = history[-30:]
                if len(window) >= 5:
                    await self.drift_monitor.check_and_emit(
                        model_version=getattr(sub, "model_version", None) or "unknown",
                        historical_scores=window[:-1],
                        recent_scores=[recent_mae],
                    )
                else:
                    logger.info(
                        "drift_check_skipped_insufficient_history",
                        target=sub.target,
                        samples=len(window),
                    )

    async def calibration_sweep_job(self) -> dict[str, Any]:
        """Scheduled calibration sweep comparing realized outcomes with historical forecasts.

        Enforces Safety Invariant 3 (Brier Score Penalization):
        Computes Brier calibration scores on resolved forecasts and penalizes overconfidence
        by conformally widening uncertainty intervals.
        """
        logger.info("calibration_sweep_job_started")
        from futuris.core.resolution import outcome_resolver
        from futuris.evaluation.calibration import CalibrationAnalyzer

        now = datetime.now(UTC)
        resolved_count = 0

        # 1. Resolve expired forecasts against observations
        if self.outcome_repo:
            unresolved = await self.outcome_repo.list_unresolved(past_horizon=True)
            if unresolved:
                connector = build_scheduler_connector()
                earliest = min(f.as_of for f in unresolved)
                obs = await connector.fetch(earliest, now)
                df = pd.DataFrame([{"timestamp": o.observed_at, "value": o.value} for o in obs])

                for f in unresolved:
                    try:
                        outcome = outcome_resolver.resolve_forecast(f, df)
                        await self.outcome_repo.record_outcome(outcome)
                        resolved_count += 1
                    except Exception as exc:
                        logger.warning(
                            "calibration_resolve_failed",
                            forecast_id=str(f.forecast_id),
                            error=str(exc),
                        )

        # 2. Compute Brier calibration metrics across resolved pairs
        analyzer = CalibrationAnalyzer()
        metrics: dict[str, Any] = {
            "brier_score": 0.0,
            "ece": 0.0,
            "resolved_count": resolved_count,
            "overconfidence_penalized": False,
            "interval_expansion_factor": 1.0,
        }

        if self.outcome_repo:
            pairs = await self.outcome_repo.list_resolved_with_forecasts(limit=1000)
            probs: list[float] = []
            actuals: list[bool] = []

            for f, o in pairs:
                if f.probability is not None and o.event_occurred is not None:
                    probs.append(float(f.probability))
                    actuals.append(bool(o.event_occurred))

            if probs and actuals:
                cal_metrics = analyzer.update_calibration_metrics(probs, actuals)
                brier = cal_metrics.get("brier_score", 0.0)
                ece = cal_metrics.get("ece", 0.0)
                metrics.update(cal_metrics)

                # Overconfidence penalty: if Brier score > 0.20 or ECE > 0.10,
                # mark penalty and compute interval expansion factor
                if brier > 0.20 or ece > 0.10:
                    metrics["overconfidence_penalized"] = True
                    expansion = round(1.0 + max(0.0, (brier - 0.20) * 2.0), 3)
                    metrics["interval_expansion_factor"] = expansion
                    logger.warning(
                        "calibration_overconfidence_detected",
                        brier_score=brier,
                        ece=ece,
                        expansion_factor=metrics["interval_expansion_factor"],
                    )
                    try:
                        from futuris.ecosystem.signals import signal_dispatcher

                        t_risk = asyncio.create_task(
                            signal_dispatcher.notify_stratex_risk_regime(
                                target="calibration:brier_overconfidence",
                                risk_report={
                                    "brier_score": brier,
                                    "ece": ece,
                                    "interval_expansion_factor": expansion,
                                    "basel_traffic_light": "yellow" if brier < 0.35 else "red",
                                    "timestamp": now.isoformat(),
                                },
                            )
                        )
                        self._in_flight_tasks.add(t_risk)
                        t_risk.add_done_callback(self._in_flight_tasks.discard)
                    except Exception as exc:
                        logger.debug("stratex_risk_dispatch_skipped", error=str(exc))
                else:
                    metrics["overconfidence_penalized"] = False
                    metrics["interval_expansion_factor"] = 1.0

                # 3. Record evaluation run
                if self.evaluation_repo:
                    model_v = (
                        pairs[0][0].model_version if pairs else "statsforecast.AutoARIMA:v1.0"
                    )
                    eval_metrics = {
                        "brier_score": float(metrics.get("brier_score", 0.0)),
                        "ece": float(metrics.get("ece", 0.0)),
                        "interval_expansion_factor": float(
                            metrics.get("interval_expansion_factor", 1.0)
                        ),
                    }
                    await self.evaluation_repo.save_run(
                        model_version=model_v,
                        dataset_name="live_telemetry_calibration",
                        metrics=eval_metrics,
                    )

        logger.info("calibration_sweep_job_completed", metrics=metrics)
        return metrics

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
            self.calibration_sweep_job, "interval", minutes=30, id="calibration_sweep_job"
        )
        self.scheduler.add_job(self.backtest_nightly_job, "cron", hour=2, id="backtest_nightly_job")
        self.scheduler.start()
        logger.info("scheduler_started")

    async def shutdown(self) -> None:
        """Gracefully drain running jobs and shutdown scheduler."""
        logger.info("scheduler_shutdown_requested")
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            await asyncio.sleep(0.01)
        if self._in_flight_tasks:
            await asyncio.gather(*self._in_flight_tasks, return_exceptions=True)
        logger.info("scheduler_shutdown_complete")
