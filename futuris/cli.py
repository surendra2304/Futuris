"""Futuris unified command-line interface for ingestion, forecasts, sweeps, and server."""

import asyncio
from datetime import UTC, datetime, timedelta

import pandas as pd
import typer
import uvicorn

from futuris.connectors.synthetic_telemetry import SyntheticTelemetryConnector
from futuris.core.lifecycle import LifecycleManager
from futuris.core.pipeline import ForecastingPipeline
from futuris.evaluation.backtest import BacktestEngine
from futuris.infra.auth import generate_api_key
from futuris.infra.logging import get_logger
from futuris.storage.db import async_session_factory
from futuris.storage.models import ApiKeyModel
from futuris.storage.repositories import (
    EventRepository,
    ForecastRepository,
    OutcomeRepository,
)

logger = get_logger("futuris.cli")
cli = typer.Typer(help="Futuris Operational Predictive-Intelligence Platform CLI")


@cli.command("ingest")
def ingest(
    days: int = typer.Option(180, "--days", "-d", help="Days of historical telemetry to fetch"),
    seed: int = typer.Option(42, "--seed", "-s", help="Random seed for synthetic generator"),
) -> None:
    """Ingest synthetic operational capacity telemetry."""

    async def _run() -> None:
        typer.echo(f"Ingesting {days} days of operational telemetry (seed={seed})...")
        connector = SyntheticTelemetryConnector(seed=seed)
        now = datetime.now(UTC)
        start = now - timedelta(days=days)
        obs = await connector.fetch(start, now)
        typer.echo(f"Successfully ingested {len(obs)} observations.")

    asyncio.run(_run())


@cli.command("forecast")
def forecast(
    target: str = typer.Option(
        "service:checkout:capacity_exceedance_24h", "--target", "-t", help="Target metric"
    ),
    horizon: str = typer.Option("24h", "--horizon", "-h", help="Forecast horizon (e.g. 24h)"),
    lookback_days: int = typer.Option(14, "--lookback", "-l", help="Historical lookback days"),
) -> None:
    """Execute end-to-end forecasting pipeline for target metric."""

    async def _run() -> None:
        typer.echo(f"Orchestrating forecast for {target} (horizon={horizon})...")
        pipeline = ForecastingPipeline()
        now = datetime.now(UTC)
        h_delta = timedelta(hours=24)
        if horizon.endswith("h"):
            h_delta = timedelta(hours=int(horizon[:-1]))
        elif horizon.endswith("d"):
            h_delta = timedelta(days=int(horizon[:-1]))

        result = await pipeline.run(
            target=target,
            as_of=now,
            horizon=h_delta,
            lookback_days=lookback_days,
        )
        f = result.forecast
        typer.echo("==================================================")
        typer.echo(f"Forecast ID:    {f.forecast_id}")
        typer.echo(f"Target:         {f.target}")
        typer.echo(f"Prediction:     {f.prediction:.2f} rpm")
        typer.echo(f"Range:          [{f.range_lower:.2f}, {f.range_upper:.2f}]")
        typer.echo(f"Probability:    {(f.probability or 0.0)*100:.1f}%")
        typer.echo(f"Confidence:     {f.confidence.value.upper()}")
        typer.echo(f"Model Version:  {f.model_version}")
        typer.echo(f"Urgency:        {result.implications.urgency.upper()}")
        typer.echo("==================================================")
        for rec in result.recommendations:
            typer.echo(f"Action: [{rec.action_type}] (Requires Approval: {rec.requires_approval})")

    asyncio.run(_run())


@cli.command("backtest")
def backtest(
    target: str = typer.Option(
        "service:checkout:capacity_exceedance_24h", "--target", "-t", help="Target metric"
    ),
    days: int = typer.Option(30, "--days", "-d", help="Backtest window length in days"),
    stride_hours: int = typer.Option(24, "--stride", "-s", help="Walk-forward stride in hours"),
) -> None:
    """Run rolling-origin walk-forward backtest evaluation."""

    async def _run() -> None:
        typer.echo(f"Executing {days}-day walk forward backtest on {target}...")
        backtester = BacktestEngine()
        now = datetime.now(UTC)
        start = now - timedelta(days=days)
        report = await backtester.run_backtest(
            target=target,
            start_date=start,
            end_date=now,
            stride_hours=stride_hours,
        )
        typer.echo(report.summary_text)

    asyncio.run(_run())


@cli.command("sweep")
def sweep() -> None:
    """Execute one lifecycle resolution, expiry, and invalidation sweep pass."""

    async def _run() -> None:
        typer.echo("Running single lifecycle sweep...")
        async with async_session_factory() as session:
            f_repo = ForecastRepository(session)
            o_repo = OutcomeRepository(session)
            e_repo = EventRepository(session)
            manager = LifecycleManager(f_repo, o_repo, e_repo)

            connector = SyntheticTelemetryConnector(seed=42)
            now = datetime.now(UTC)
            obs = await connector.fetch(now - timedelta(days=2), now)

            df = pd.DataFrame([{"timestamp": o.observed_at, "value": o.value} for o in obs])
            report = await manager.run_lifecycle_sweep(observations_df=df, as_of=now)
            typer.echo(
                f"Lifecycle Sweep: Resolved={report.resolved_count}, "
                f"Invalidated={report.invalidated_count}, Expired={report.expired_count}"
            )

    asyncio.run(_run())


@cli.command("create-admin-key")
def create_admin_key(
    label: str = typer.Option("bootstrap-admin", "--label", "-l", help="Key identifier label"),
) -> None:
    """Bootstrap a root admin API key with full platform permissions."""

    async def _run() -> None:
        plain_key, key_hash = generate_api_key(prefix="futuris_admin")
        now = datetime.now(UTC)
        async with async_session_factory() as session:
            record = ApiKeyModel(
                key_hash=key_hash,
                label=label,
                role="admin",
                created_at=now,
                revoked_at=None,
            )
            session.add(record)
            await session.commit()

        typer.echo("==================================================")
        typer.echo("FUTURIS ADMIN API KEY CREATED (STORE SECURELY):")
        typer.echo(f"Key Label: {label}")
        typer.echo("Role:      ADMIN")
        typer.echo(f"API Key:   {plain_key}")
        typer.echo("==================================================")

    asyncio.run(_run())


@cli.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Bind host"),
    port: int = typer.Option(8000, "--port", "-p", help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload"),
) -> None:
    """Start the FastAPI public API server."""
    typer.echo(f"Starting Futuris API Server on http://{host}:{port}")
    uvicorn.run("futuris.api.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    cli()
