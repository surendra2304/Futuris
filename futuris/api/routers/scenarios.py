"""Scenario generation, Monte Carlo simulations, and multi-scenario comparison router."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from futuris.api.deps import get_forecast_repo, get_scenario_repo
from futuris.scenarios.engine import (
    ScenarioComparison,
    ScenarioEngine,
    ScenarioResult,
)
from futuris.scenarios.spec import ScenarioSpec
from futuris.storage.repositories import ForecastRepository, ScenarioRepository

router = APIRouter(prefix="/v1/forecasts/{forecast_id}/scenarios", tags=["Scenarios"])


class RunScenariosRequest(BaseModel):
    """Payload to execute scenario simulations on an existing parent forecast."""

    scenarios: list[ScenarioSpec] = Field(..., min_length=1)
    use_monte_carlo: bool = True
    num_samples: int = 1000


@router.post("", response_model=list[ScenarioResult], summary="Run Scenarios for Forecast")
async def run_scenarios(
    forecast_id: UUID,
    req: RunScenariosRequest,
    forecast_repo: ForecastRepository = Depends(get_forecast_repo),
    scenario_repo: ScenarioRepository = Depends(get_scenario_repo),
) -> list[ScenarioResult]:
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

    return results


@router.post("/compare", response_model=ScenarioComparison, summary="Compare Scenarios")
async def compare_scenarios(
    forecast_id: UUID,
    req: RunScenariosRequest,
    forecast_repo: ForecastRepository = Depends(get_forecast_repo),
    scenario_repo: ScenarioRepository = Depends(get_scenario_repo),
) -> ScenarioComparison:
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

    return engine.compare(base_forecast=f, results=results)
