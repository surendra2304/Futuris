"""Tests for telemetry ingestion, calibration sweeps, and Brier penalization."""

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from futuris.connectors.base import Observation
from futuris.core.enums import ConfidenceLevel, EvidenceClass, ForecastStatus
from futuris.core.schemas import Forecast
from futuris.infra.scheduler import ForecastScheduler
from futuris.storage.models import Base
from futuris.storage.repositories import (
    EvaluationRepository,
    ForecastRepository,
    ObservationRepository,
    OutcomeRepository,
)

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_ingest_job_persists_observations(db_session: AsyncSession) -> None:
    obs_repo = ObservationRepository(db_session)
    scheduler = ForecastScheduler(observation_repo=obs_repo)

    now = datetime.now(UTC)
    mock_obs = [
        Observation(
            observed_at=now - timedelta(minutes=5),
            source="synthetic_telemetry",
            series_id="service:checkout:requests_per_second",
            value=1200.0,
            unit="req/s",
        ),
        Observation(
            observed_at=now,
            source="synthetic_telemetry",
            series_id="service:checkout:requests_per_second",
            value=1250.0,
            unit="req/s",
        ),
    ]

    with patch("futuris.infra.scheduler.build_scheduler_connector") as mock_conn_builder:
        mock_connector = MagicMock()
        mock_connector.fetch = AsyncMock(return_value=mock_obs)
        mock_conn_builder.return_value = mock_connector

        points = await scheduler.ingest_job()
        assert points == 2

    # Verify observations in repository
    stored = await obs_repo.list_by_series("service:checkout:requests_per_second")
    assert len(stored) == 2


@pytest.mark.asyncio
async def test_calibration_sweep_resolves_and_penalizes_overconfidence(
    db_session: AsyncSession,
) -> None:
    f_repo = ForecastRepository(db_session)
    o_repo = OutcomeRepository(db_session)
    eval_repo = EvaluationRepository(db_session)

    scheduler = ForecastScheduler(
        forecast_repo=f_repo,
        outcome_repo=o_repo,
        evaluation_repo=eval_repo,
    )

    now = datetime.now(UTC)
    t_past = now - timedelta(hours=2)

    # Insert a forecast that is now expired (expires_at in past)
    f_id = uuid4()
    f = Forecast(
        forecast_id=f_id,
        target="service:checkout:capacity_exceedance_24h",
        as_of=t_past - timedelta(hours=1),
        horizon=timedelta(hours=1),
        expires_at=t_past,
        prediction=4200.0,
        range_lower=4100.0,
        range_upper=4300.0,
        probability=0.95,  # High overconfident probability
        confidence=ConfidenceLevel.HIGH,
        model_version="statsforecast.AutoARIMA:v1.0",
        review_at=t_past,
        status=ForecastStatus.ACTIVE,
        evidence_class=EvidenceClass.LIVE,
    )
    await f_repo.create(f)

    # Mock connector telemetry observations with full coverage where demand did NOT exceed threshold
    mock_obs = [
        Observation(
            observed_at=t_past - timedelta(minutes=5 * i),
            source="synthetic_telemetry",
            series_id="service:checkout:capacity_exceedance_24h",
            value=2500.0 + float(i),
            unit="rpm",
        )
        for i in range(13)
    ]

    with patch("futuris.infra.scheduler.build_scheduler_connector") as mock_conn_builder:
        mock_connector = MagicMock()
        mock_connector.fetch = AsyncMock(return_value=mock_obs)
        mock_conn_builder.return_value = mock_connector

        metrics = await scheduler.calibration_sweep_job()

        assert metrics["resolved_count"] >= 1
        # Target-level breakdown
        assert "target_breakdown" in metrics
        assert "service:checkout:capacity_exceedance_24h" in metrics["target_breakdown"]
        tb = metrics["target_breakdown"]["service:checkout:capacity_exceedance_24h"]
        assert tb["brier_score"] > 0.20
        assert tb["resolved_count"] >= 1

        # Predicted 0.95 probability but outcome event was FALSE (did not exceed)
        # Brier score should be (0.95 - 0.0)^2 = ~0.9025
        assert metrics["brier_score"] > 0.20
        # Overconfidence penalization must trigger
        assert metrics["overconfidence_penalized"] is True
        assert metrics["interval_expansion_factor"] > 1.0


@pytest.mark.asyncio
async def test_scheduler_registers_and_shuts_down() -> None:
    scheduler = ForecastScheduler()
    scheduler.start()
    try:
        job_ids = [j.id for j in scheduler.scheduler.get_jobs()]
        assert "calibration_sweep_job" in job_ids
        assert "stream_risk_pulse_job" in job_ids
        assert "ingest_job" in job_ids
        assert "forecast_refresh_job" in job_ids
    finally:
        await scheduler.shutdown()
        assert not scheduler.scheduler.running


@pytest.mark.asyncio
async def test_stream_risk_pulse_job_broadcasts_fan_chart_and_risk(monkeypatch) -> None:
    """Verify stream_risk_pulse_job computes tail risk and broadcasts fan chart to stream_hub."""
    import asyncio

    from futuris.api.routers.streaming import stream_hub
    from futuris.connectors.trading_bot import TradingBotConnector

    # Mock connector to avoid external network calls
    monkeypatch.setattr(
        TradingBotConnector,
        "discover_active_trade_pairs",
        lambda self: asyncio.sleep(0, result=["BTCUSDT", "ETHUSDT"]),
    )
    monkeypatch.setattr(
        TradingBotConnector,
        "fetch_active_positions",
        lambda self: asyncio.sleep(
            0,
            result=[{
                "symbol": "BTCUSDT",
                "side": "LONG",
                "size": 1.0,
                "current_price": 68000.0,
                "margin": 6800.0,
                "unrealized_pnl": 500.0,
            }],
        ),
    )

    # Listen on stream_hub for risk_portfolio_update and fan_chart_simulation
    sub_id, queue = await stream_hub.register(target_filter=None)

    try:
        scheduler = ForecastScheduler()
        result = await scheduler.stream_risk_pulse_job()

        assert "risk_payload" in result
        assert "fan_chart" in result
        fc = result["fan_chart"]
        assert fc["prediction_is_not_authorization"] is True
        assert fc["asset"] in {"BTCUSDT", "ETHUSDT"}
        assert fc["horizon_hours"] == 24
        assert len(fc["quantiles"]["p10"]) == 24
        assert len(fc["quantiles"]["p50"]) == 24
        assert len(fc["quantiles"]["p90"]) == 24

        # Read broadcast events from stream_hub
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        event_types = {e["type"] for e in events}
        assert "risk_portfolio_update" in event_types
        assert "fan_chart_simulation" in event_types
    finally:
        await stream_hub.unregister(sub_id)


@pytest.mark.asyncio
async def test_scheduler_forecast_refresh_job(db_session: AsyncSession) -> None:
    f_repo = ForecastRepository(db_session)
    scheduler = ForecastScheduler(forecast_repo=f_repo)
    refreshed = await scheduler.forecast_refresh_job()
    assert len(refreshed) >= 1
    assert refreshed[0].target == "service:checkout:capacity_exceedance_24h"
