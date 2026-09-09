"""Deterministic demo seeder generating telemetry, backtests, forecasts, scenarios, and sweeps."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from futuris.connectors.synthetic_telemetry import SyntheticTelemetryConnector
from futuris.core.enums import ScenarioType
from futuris.core.lifecycle import LifecycleManager
from futuris.core.pipeline import ForecastingPipeline
from futuris.evaluation.backtest import BacktestEngine
from futuris.evaluation.calibration import CalibrationAnalyzer
from futuris.infra.logging import get_logger
from futuris.scenarios.engine import ScenarioEngine
from futuris.scenarios.spec import ScenarioSpec
from futuris.storage.db import async_session_factory
from futuris.storage.models import ModelRegistryModel
from futuris.storage.repositories import (
    EvaluationRepository,
    EventRepository,
    ForecastRepository,
    OutcomeRepository,
    ScenarioRepository,
)

logger = get_logger("futuris.demo")


class DemoSeeder:
    """Orchestrates deterministic bootstrapping of synthetic telemetry and forecasts."""

    def __init__(self, seed: int = 42, session: AsyncSession | None = None) -> None:
        self.seed = seed
        self.session = session

    async def _execute_with_session(
        self,
        session: AsyncSession,
        days: int,
        backtest_days: int,
        target: str,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)

        # 1. Telemetry Ingestion
        connector = SyntheticTelemetryConnector(seed=self.seed)
        start_time = now - timedelta(days=days)
        observations = await connector.fetch(start_time, now)
        obs_df = pd.DataFrame(
            [{"timestamp": o.observed_at, "value": o.value} for o in observations]
        )

        f_repo = ForecastRepository(session)
        o_repo = OutcomeRepository(session)
        e_repo = EventRepository(session)
        s_repo = ScenarioRepository(session)
        _ = EvaluationRepository(session)
        lifecycle_mgr = LifecycleManager(f_repo, o_repo, e_repo)

        # 2. Register Active Model Adapter
        active_m = ModelRegistryModel(
            model_version="auto_arima@v1",
            family="arima",
            config_hash="default_cfg",
            is_active=True,
            promoted_at=now - timedelta(days=20),
            benchmark_scores={"mae": 40.2, "ece": 0.045, "coverage": 0.91},
        )
        session.add(active_m)
        await session.flush()

        # 3. Run Live Pipeline Forecast
        pipeline = ForecastingPipeline()
        res = await pipeline.run(
            target=target,
            as_of=now,
            horizon=timedelta(hours=24),
            lookback_days=min(14, days),
        )
        live_forecast = res.forecast
        await f_repo.create(live_forecast)

        # 3.1 Seed Market Forecasts for Stratex & Ecosystem
        from futuris.core.schemas import Driver, EvidenceRef, Forecast
        from futuris.core.enums import ConfidenceLevel, ForecastStatus, SignalClass, SourceTrust

        market_evidence = [
            EvidenceRef(
                evidence_id=uuid4(),
                source="intelx:market_research",
                source_trust=SourceTrust.HIGH,
                signal_class=SignalClass.EXTERNAL,
                as_of=now,
                snapshot_path="data/storage/intelx_seed_market_snapshot.parquet",
                content_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            )
        ]

        market_forecast_btc = Forecast(
            forecast_id=uuid4(),
            target="market:crypto:BTCUSDT:volatility_24h",
            as_of=now,
            horizon=timedelta(hours=24),
            expires_at=now + timedelta(hours=24),
            review_at=now + timedelta(hours=6),
            prediction=0.38,
            range_lower=-2.8,
            range_upper=4.6,
            probability=0.34,
            confidence=ConfidenceLevel.HIGH,
            drivers=[
                Driver(
                    name="intelx:spot_etf_inflows",
                    direction="positive",
                    strength=0.92,
                    leading_or_lagging="leading",
                    evidence_refs=[uuid4()],
                ),
                Driver(
                    name="stratex_volatility_idx",
                    direction="neutral",
                    strength=0.75,
                    leading_or_lagging="leading",
                    evidence_refs=[uuid4()],
                ),
            ],
            evidence=market_evidence,
            assumptions=["IntelX market research: Bullish spot accumulation", "Stratex equity: $5000+ healthy balance"],
            model_version="statsforecast:market_volatility@v2",
            status=ForecastStatus.ACTIVE,
        )
        await f_repo.create(market_forecast_btc)

        market_forecast_eth = Forecast(
            forecast_id=uuid4(),
            target="market:crypto:ETHUSDT:volatility_24h",
            as_of=now,
            horizon=timedelta(hours=24),
            expires_at=now + timedelta(hours=24),
            review_at=now + timedelta(hours=6),
            prediction=0.42,
            range_lower=-3.2,
            range_upper=5.1,
            probability=0.39,
            confidence=ConfidenceLevel.MEDIUM,
            drivers=[
                Driver(
                    name="intelx:l2_settlement_acceleration",
                    direction="positive",
                    strength=0.88,
                    leading_or_lagging="leading",
                    evidence_refs=[uuid4()],
                )
            ],
            evidence=market_evidence,
            assumptions=["IntelX network expansion signals", "Stratex portfolio risk bounds respected"],
            model_version="statsforecast:market_volatility@v2",
            status=ForecastStatus.ACTIVE,
        )
        await f_repo.create(market_forecast_eth)

        # 4. Generate Family of Scenarios
        scenario_engine = ScenarioEngine(scenario_repo=s_repo)
        scenarios = [
            ScenarioSpec(
                spec_id=uuid4(),
                name="Upside Growth (+20% Demand)",
                scenario_type=ScenarioType.UPSIDE,
                assumption_overrides={"demand": live_forecast.prediction * 1.20},
                rationale="Anticipating marketing flash promotion",
            ),
            ScenarioSpec(
                spec_id=uuid4(),
                name="Stress Test (+40% Demand, -20% Capacity)",
                scenario_type=ScenarioType.STRESS,
                assumption_overrides={
                    "demand": live_forecast.prediction * 1.40,
                    "capacity": 3200.0,
                },
                rationale="Simulating major node cluster degradation during peak sale",
            ),
            ScenarioSpec(
                spec_id=uuid4(),
                name="Downside Trough (-30% Demand)",
                scenario_type=ScenarioType.DOWNSIDE,
                assumption_overrides={"demand": live_forecast.prediction * 0.70},
                rationale="Off-peak maintenance window",
            ),
        ]

        scenario_results = []
        for spec in scenarios:
            s_res = await scenario_engine.run_scenario(
                base_forecast=live_forecast,
                spec=spec,
                use_monte_carlo=False,
            )
            scenario_results.append(s_res)

        comparison = scenario_engine.compare(live_forecast, scenario_results)

        # 5. Execute Backtest
        backtester = BacktestEngine(forecast_repo=f_repo, outcome_repo=o_repo)
        bt_report = await backtester.run_backtest(
            target=target,
            start_date=now - timedelta(days=backtest_days),
            end_date=now - timedelta(days=1),
            stride_hours=24,
            horizon=timedelta(hours=24),
        )

        # 6. Force Lifecycle Sweep
        sweep_report = await lifecycle_mgr.run_lifecycle_sweep(
            observations_df=obs_df, as_of=now
        )

        # 7. Compute Calibration Reliability
        analyzer = CalibrationAnalyzer()
        cal_curve = analyzer.compute_reliability_curve(
            predicted_probs=[0.1, 0.2, 0.7, 0.85, 0.9],
            actual_outcomes=[False, False, True, True, True],
        )

        top_div = (
            comparison.divergence_ranking[0] if comparison.divergence_ranking else None
        )

        return {
            "telemetry_points": len(observations),
            "live_forecast_id": live_forecast.forecast_id,
            "live_prediction": live_forecast.prediction,
            "live_probability": live_forecast.probability,
            "live_confidence": live_forecast.confidence.value,
            "scenarios_evaluated": len(scenario_results),
            "top_divergence": top_div,
            "backtest_runs": bt_report.total_forecasts,
            "resolved_outcomes": sweep_report.resolved_count,
            "calibration_ece": cal_curve.expected_calibration_error,
        }

    async def run(
        self,
        days: int = 180,
        backtest_days: int = 30,
        target: str = "service:checkout:capacity_exceedance_24h",
    ) -> dict[str, Any]:
        """Execute full end-to-end demo bootstrapping sequence."""
        logger.info("demo_bootstrap_started", days=days, seed=self.seed)

        if self.session:
            return await self._execute_with_session(
                self.session, days, backtest_days, target
            )

        from futuris.storage.db import engine
        from futuris.storage.models import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with async_session_factory() as session:
            res = await self._execute_with_session(
                session, days, backtest_days, target
            )
            await session.commit()
            return res
