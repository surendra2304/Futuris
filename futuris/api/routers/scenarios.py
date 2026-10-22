"""Scenario generation, Monte Carlo simulations, and multi-scenario comparison router."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from futuris.api.deps import get_db_session, get_forecast_repo, get_scenario_repo
from futuris.api.routers.streaming import stream_hub
from futuris.infra.audit import AuditLogger
from futuris.infra.auth import AllowAnonymousRead, RequireAnalyst
from futuris.infra.logging import get_logger
from futuris.scenarios.engine import (
    ScenarioComparison,
    ScenarioEngine,
    ScenarioResult,
)
from futuris.scenarios.monte_carlo import DriverDistribution, MonteCarloScenarioEngine
from futuris.scenarios.spec import ScenarioSpec
from futuris.storage.repositories import ForecastRepository, ScenarioRepository

logger = get_logger("futuris.api.scenarios")

router = APIRouter(prefix="/v1/forecasts/{forecast_id}/scenarios", tags=["Scenarios"])
fan_chart_router = APIRouter(prefix="/v1/scenarios", tags=["Scenarios"])


class FanChartRequest(BaseModel):
    """Payload to configure multi-step Monte Carlo quantile fan-chart simulation."""

    target: str = Field(default="BTCUSDT", description="Target metric or asset")
    base_value: float = Field(default=100.0, description="Initial baseline value")
    horizon_steps: int = Field(default=12, ge=1, le=100, description="Horizon steps into future")
    num_samples: int = Field(
        default=1000, ge=100, le=10000, description="Number of Monte Carlo paths"
    )
    drift: float = Field(default=0.001, description="Mean drift per step")
    volatility: float = Field(
        default=0.03, ge=0.0001, le=1.0, description="Volatility std per step"
    )
    capacity_threshold: float | None = Field(
        default=None, description="Optional threshold for exceedance prob"
    )
    use_antithetic_variates: bool = Field(
        default=True, description="Enable antithetic variance reduction"
    )


class FanChartSlicePayload(BaseModel):
    step: int
    mean: float
    std: float
    p05: float
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    p95: float
    p99: float
    exceedance_probability: float | None = None


class FanChartResponse(BaseModel):
    target: str
    base_value: float
    num_samples: int
    horizon_steps: int
    fan_chart: list[FanChartSlicePayload]
    trajectories_sample: list[list[float]]
    driver_names: list[str]
    sample_correlations: dict[str, dict[str, float]]
    prediction_is_not_authorization: bool = True
    timestamp: datetime



class RunScenariosRequest(BaseModel):
    """Payload to execute scenario simulations on an existing parent forecast."""

    scenarios: list[ScenarioSpec] = Field(..., min_length=1)
    use_monte_carlo: bool = True
    num_samples: int = 1000


@router.post("", response_model=list[ScenarioResult], summary="Run Scenarios for Forecast")
async def run_scenarios(
    req: RunScenariosRequest,
    user: RequireAnalyst,
    forecast_id: UUID,
    forecast_repo: ForecastRepository = Depends(get_forecast_repo),
    scenario_repo: ScenarioRepository = Depends(get_scenario_repo),
    session: AsyncSession = Depends(get_db_session),
) -> list[ScenarioResult]:
    _ = user  # auth dependency; identity handled by the role guard
    """Execute scenario specifications against parent forecast without mutating base data."""
    f = await forecast_repo.get(forecast_id)
    if not f:
        raise HTTPException(status_code=404, detail="Parent forecast not found")

    engine = ScenarioEngine(scenario_repo=scenario_repo)
    results: list[ScenarioResult] = []

    for spec in req.scenarios:
        res = await engine.run_scenario(
            base_forecast=f,
            spec=spec,
            use_monte_carlo=req.use_monte_carlo,
            num_samples=req.num_samples,
        )
        results.append(res)

    await AuditLogger(session).log_mutation(
        actor_label=user.label,
        action="run_scenarios",
        entity="forecast",
        entity_id=str(forecast_id),
        payload={
            "scenarios": [s.name for s in req.scenarios],
            "monte_carlo": req.use_monte_carlo,
            "num_samples": req.num_samples,
        },
    )

    return results


@router.post("/compare", response_model=ScenarioComparison, summary="Compare Scenarios")
async def compare_scenarios(
    forecast_id: UUID,
    req: RunScenariosRequest,
    user: RequireAnalyst,
    forecast_repo: ForecastRepository = Depends(get_forecast_repo),
    scenario_repo: ScenarioRepository = Depends(get_scenario_repo),
    session: AsyncSession = Depends(get_db_session),
) -> ScenarioComparison:
    _ = user  # auth dependency; identity handled by the role guard
    """Run and compare diverging scenarios side-by-side with sensitivity ranking."""
    f = await forecast_repo.get(forecast_id)
    if not f:
        raise HTTPException(status_code=404, detail="Parent forecast not found")

    engine = ScenarioEngine(scenario_repo=scenario_repo)
    results: list[ScenarioResult] = []

    for spec in req.scenarios:
        res = await engine.run_scenario(
            base_forecast=f,
            spec=spec,
            use_monte_carlo=req.use_monte_carlo,
            num_samples=req.num_samples,
        )
        results.append(res)

    comparison = engine.compare(base_forecast=f, results=results)

    await AuditLogger(session).log_mutation(
        actor_label=user.label,
        action="compare_scenarios",
        entity="forecast",
        entity_id=str(forecast_id),
        payload={
            "scenarios": [s.name for s in req.scenarios],
            "top_divergence": comparison.divergence_ranking[:1],
        },
    )

    return comparison


def _execute_fan_chart_simulation(req: FanChartRequest) -> FanChartResponse:
    """Run Monte Carlo trajectories and compile multi-step quantile fan chart."""
    engine = MonteCarloScenarioEngine(seed=42)
    drivers = [
        DriverDistribution(
            name="drift_shock",
            dist_type="normal",
            param1=req.drift,
            param2=req.volatility,
        ),
        DriverDistribution(
            name="volatility_shock",
            dist_type="normal",
            param1=0.0,
            param2=req.volatility * 1.5,
        ),
    ]

    def _transfer(driver_vals: dict[str, float], prev: float, _step: int) -> float:
        shock = driver_vals.get("drift_shock", 0.0) + 0.3 * driver_vals.get("volatility_shock", 0.0)
        return float(prev * (1.0 + shock))

    sim_res = engine.simulate_trajectories(
        base_value=req.base_value,
        drivers=drivers,
        transfer_function=_transfer,
        horizon_steps=req.horizon_steps,
        num_samples=req.num_samples,
        capacity_threshold=req.capacity_threshold,
        use_antithetic_variates=req.use_antithetic_variates,
    )

    slices = [
        FanChartSlicePayload(
            step=s.step,
            mean=round(s.mean, 4),
            std=round(s.std, 4),
            p05=round(s.p05, 4),
            p10=round(s.p10, 4),
            p25=round(s.p25, 4),
            p50=round(s.p50, 4),
            p75=round(s.p75, 4),
            p90=round(s.p90, 4),
            p95=round(s.p95, 4),
            p99=round(s.p99, 4),
            exceedance_probability=(
                round(s.exceedance_probability, 4)
                if s.exceedance_probability is not None
                else None
            ),
        )
        for s in sim_res.fan_chart
    ]

    return FanChartResponse(
        target=req.target,
        base_value=req.base_value,
        num_samples=sim_res.num_samples,
        horizon_steps=sim_res.horizon_steps,
        fan_chart=slices,
        trajectories_sample=[[round(v, 4) for v in path] for path in sim_res.trajectories_sample],
        driver_names=sim_res.driver_names,
        sample_correlations=sim_res.sample_correlations,
        prediction_is_not_authorization=True,
        timestamp=datetime.now(UTC),
    )


@fan_chart_router.post(
    "/monte-carlo/fan-chart",
    response_model=FanChartResponse,
    summary="Generate Monte Carlo Quantile Fan Chart",
)
async def generate_fan_chart(
    req: FanChartRequest,
    user: AllowAnonymousRead = None,
) -> FanChartResponse:
    _ = user
    """Generate multi-step quantile fan chart (p05..p99) and broadcast via SSE/WebSocket."""
    resp = _execute_fan_chart_simulation(req)
    try:
        await stream_hub.broadcast(
            "fan_chart_simulation", resp.model_dump(mode="json"), target=req.target
        )
    except Exception as exc:
        logger.warning("fan_chart_stream_broadcast_failed", target=req.target, error=str(exc))
    return resp


@router.post(
    "/fan-chart",
    response_model=FanChartResponse,
    summary="Generate Fan Chart for Parent Forecast",
)
async def forecast_fan_chart(
    forecast_id: UUID,
    user: RequireAnalyst,
    horizon_steps: int = 12,
    num_samples: int = 1000,
    forecast_repo: ForecastRepository = Depends(get_forecast_repo),
) -> FanChartResponse:
    _ = user
    """Generate quantile fan chart directly anchored to parent forecast value."""
    f = await forecast_repo.get(forecast_id)
    if not f:
        raise HTTPException(status_code=404, detail="Parent forecast not found")

    req = FanChartRequest(
        target=f.target,
        base_value=float(f.prediction),
        horizon_steps=horizon_steps,
        num_samples=num_samples,
    )
    resp = _execute_fan_chart_simulation(req)
    try:
        await stream_hub.broadcast(
            "fan_chart_simulation", resp.model_dump(mode="json"), target=f.target
        )
    except Exception as exc:
        logger.warning("fan_chart_stream_broadcast_failed", target=f.target, error=str(exc))
    return resp

