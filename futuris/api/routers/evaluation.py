"""Evaluation reports, walk-forward backtests, and calibration curve router."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from futuris.api.deps import get_db_session, get_outcome_repo
from futuris.core.enums import EvidenceClass
from futuris.evaluation.calibration import CalibrationAnalyzer, ReliabilityCurve
from futuris.infra.auth import AllowAnonymousRead
from futuris.storage.repositories import EvaluationRepository, OutcomeRepository

router = APIRouter(prefix="/v1/evaluation", tags=["Evaluation & Calibration"])


class CalibrationResponse(BaseModel):
    """Binned reliability curve data for calibration diagrams."""

    target: str
    bin_centers: list[float]
    observed_frequencies: list[float]
    bin_counts: list[int]
    expected_calibration_error: float
    sample_count: int = 0
    calibration_method: str = "empirical_binned"
    data_freshness: str = "live"
    evidence_class: EvidenceClass | None = None


@router.get("/calibration", response_model=CalibrationResponse, summary="Get Calibration Curves")
async def get_calibration(
    user: AllowAnonymousRead,
    target: str = Query("service:checkout:capacity_exceedance_24h"),
    outcome_repo: OutcomeRepository = Depends(get_outcome_repo),
) -> CalibrationResponse:
    """Empirical calibration for one target, from persisted (probability, outcome) pairs.

    When nothing has been resolved for the target there is no reliability curve
    to draw: the response reports ``insufficient_data`` instead of substituting
    illustrative points.
    """
    _ = user
    pairs = await outcome_repo.list_resolved_with_forecasts(target_prefix=target, limit=5000)
    probs: list[float] = []
    actuals: list[bool] = []
    for forecast, outcome in pairs:
        if forecast.probability is None or outcome.event_occurred is None:
            continue
        probs.append(forecast.probability)
        actuals.append(bool(outcome.event_occurred))

    if not probs:
        return CalibrationResponse(
            target=target,
            bin_centers=[],
            observed_frequencies=[],
            bin_counts=[],
            expected_calibration_error=0.0,
            sample_count=0,
            calibration_method="empirical_binned",
            data_freshness="insufficient_data",
            evidence_class=None,
        )

    curve: ReliabilityCurve = CalibrationAnalyzer().compute_reliability_curve(probs, actuals)
    return CalibrationResponse(
        target=target,
        bin_centers=curve.bin_centers,
        observed_frequencies=curve.observed_frequencies,
        bin_counts=curve.bin_counts,
        expected_calibration_error=curve.calibration_error,
        sample_count=len(actuals),
        calibration_method="empirical_binned",
        data_freshness="live_persisted",
        evidence_class=EvidenceClass.DERIVED,
    )


@router.get("/backtests", summary="List Evaluation Backtest Runs")
async def list_backtests(
    user: AllowAnonymousRead,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[dict[str, Any]]:
    """List persisted evaluation runs with their measured metrics.

    The endpoint used to return a hardcoded report (30 forecasts, MAE 45.2,
    89% coverage) for a database that had never been backtested. It now serves
    the evaluation_runs table and an empty list when nothing has run.
    """
    _ = user
    runs = await EvaluationRepository(session).list_runs()
    return [
        {
            "run_id": str(run["run_id"]),
            "model_version": run["model_version"],
            "dataset_name": run["dataset_name"],
            "metrics": run["metrics"],
            "created_at": run["created_at"],
            "evidence_class": EvidenceClass.DERIVED,
        }
        for run in runs
    ]


@router.get("/backtests/{run_id}", summary="Get Full Backtest Report")
async def get_backtest_report(
    run_id: str,
    user: AllowAnonymousRead,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Retrieve a persisted backtest report by ID, or 404 if it does not exist."""
    _ = user
    runs = await EvaluationRepository(session).list_runs(limit=500)
    for run in runs:
        if str(run["run_id"]) == run_id:
            return {
                "run_id": run_id,
                "model_version": run["model_version"],
                "dataset_name": run["dataset_name"],
                "metrics": run["metrics"],
                "created_at": run["created_at"],
                "evidence_class": EvidenceClass.DERIVED,
            }
    raise HTTPException(status_code=404, detail=f"No evaluation run '{run_id}'.")
