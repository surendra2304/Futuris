"""Universal FRIDAY Universe prediction router serving all 9 ecosystem subsystems."""

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from futuris.api.deps import get_db_session, get_forecast_repo
from futuris.core.enums import ForecastStatus
from futuris.core.schemas import Forecast
from futuris.core.universe_forecasting import (
    generate_universe_forecast,
    refresh_all_within_budget,
    refresh_missing_within_budget,
)
from futuris.core.universe_domains import (
    UNIVERSE_TARGETS,
    DomainTargetSpec,
    RiskLevel,
    UniverseDomain,
    evaluate_risk_level,
    get_target_spec,
)
from futuris.ecosystem.adapters import ecosystem_adapter
from futuris.infra.config import settings
from futuris.infra.logging import get_logger
from futuris.storage.models import ForecastModel
from futuris.storage.repositories import ForecastRepository


logger = get_logger("futuris.api.predictions")

router = APIRouter(prefix="/v1/predictions", tags=["Universe Predictions"])


class UniversePredictionRequest(BaseModel):
    target: str = Field(
        ...,
        description="Target metric identifier across any FRIDAY Universe domain.",
        json_schema_extra={"example": "sentinel:security:threat_anomaly_risk_24h"},
    )
    domain: UniverseDomain | None = Field(
        default=None,
        description="Optional explicit universe domain. Inferred from target if omitted.",
    )
    horizon: str = Field(
        default="24h",
        description="Forecast horizon duration string (e.g. '1h', '6h', '24h', '7d').",
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Dynamic contextual telemetry supplied by the requesting agent.",
    )


class UniversePredictionResponse(BaseModel):
    forecast_id: UUID
    target: str
    target_name: str
    domain: UniverseDomain
    point_prediction: float
    unit: str
    range_lower: float
    range_upper: float
    probability: float | None
    confidence: str
    risk_level: RiskLevel
    mitigation_action: str
    interpretation: str
    drivers: list[str]
    model_version: str
    as_of: datetime
    expires_at: datetime
    intelx_context_included: bool


class TargetPostureItem(BaseModel):
    target: str
    name: str
    domain: UniverseDomain
    point_prediction: float
    unit: str
    range_lower: float
    range_upper: float
    probability: float | None
    confidence: str
    risk_level: RiskLevel
    mitigation_action: str
    interpretation: str
    last_updated: datetime


class DomainPostureSummary(BaseModel):
    domain: UniverseDomain
    display_name: str
    risk_level: RiskLevel
    target_count: int
    targets: list[TargetPostureItem]


class UniverseMatrixResponse(BaseModel):
    ecosystem_health_score: float
    overall_posture: RiskLevel
    total_domains: int
    total_active_targets: int
    domains: list[DomainPostureSummary]
    timestamp: datetime


@router.post(
    "/predict",
    response_model=UniversePredictionResponse,
    status_code=status.HTTP_200_OK,
    summary="Request On-Demand Forecast for Any FRIDAY Universe Agent",
)
async def request_universe_prediction(
    req: UniversePredictionRequest,
    session: AsyncSession = Depends(get_db_session),
) -> UniversePredictionResponse:
    """Universal predictive intelligence endpoint for all 9 FRIDAY Universe subsystems."""
    spec = get_target_spec(req.target)
    fc = await generate_universe_forecast(req.target, context=req.context, session=session)

    risk = evaluate_risk_level(spec, fc.prediction, fc.probability)
    interp = spec.interpretation_template.format(
        prediction=fc.prediction,
        probability=(fc.probability or 0.0) * 100.0,
        unit=spec.unit,
    )

    intelx_included = any(d.name.startswith("intelx:") for d in fc.drivers)

    return UniversePredictionResponse(
        forecast_id=fc.forecast_id,
        target=fc.target,
        target_name=spec.name,
        domain=spec.domain,
        point_prediction=fc.prediction,
        unit=spec.unit,
        range_lower=fc.range_lower,
        range_upper=fc.range_upper,
        probability=fc.probability,
        confidence=fc.confidence.value.upper(),
        risk_level=risk,
        mitigation_action=spec.mitigation_action,
        interpretation=interp,
        drivers=[d.name for d in fc.drivers],
        model_version=fc.model_version,
        as_of=fc.as_of,
        expires_at=fc.expires_at,
        intelx_context_included=intelx_included,
    )


@router.get(
    "/matrix",
    response_model=UniverseMatrixResponse,
    summary="Get Complete FRIDAY Universe Predictive Risk Matrix",
)
async def get_universe_matrix(
    session: AsyncSession = Depends(get_db_session),
) -> UniverseMatrixResponse:
    """Aggregates real-time risk posture and latest forecasts across all 9 FRIDAY Universe pillars."""
    repo = ForecastRepository(session)
    active_forecasts = await repo.list_by_status(ForecastStatus.ACTIVE)

    # Index latest forecast by target
    latest_by_target: dict[str, Forecast] = {}
    for f in sorted(active_forecasts, key=lambda x: x.as_of):
        latest_by_target[f.target] = f

    # Ensure all registered targets have a forecast represented
    await refresh_missing_within_budget(session, latest_by_target)

    # Group by domain
    domain_map: dict[UniverseDomain, list[TargetPostureItem]] = {d: [] for d in UniverseDomain}
    all_risks: list[RiskLevel] = []

    for target, spec in UNIVERSE_TARGETS.items():
        fc = latest_by_target.get(target)
        if not fc:
            continue
        risk = evaluate_risk_level(spec, fc.prediction, fc.probability)
        all_risks.append(risk)
        interp = spec.interpretation_template.format(
            prediction=fc.prediction,
            probability=(fc.probability or 0.0) * 100.0,
            unit=spec.unit,
        )

        domain_map[spec.domain].append(
            TargetPostureItem(
                target=spec.target,
                name=spec.name,
                domain=spec.domain,
                point_prediction=fc.prediction,
                unit=spec.unit,
                range_lower=fc.range_lower,
                range_upper=fc.range_upper,
                probability=fc.probability,
                confidence=fc.confidence.value.upper(),
                risk_level=risk,
                mitigation_action=spec.mitigation_action,
                interpretation=interp,
                last_updated=fc.as_of,
            )
        )

    domain_summaries: list[DomainPostureSummary] = []
    display_names = {
        UniverseDomain.FRIDAY: "FRIDAY Core",
        UniverseDomain.SENTINEL: "Sentinel Security",
        UniverseDomain.CORTEX: "Cortex Cognition",
        UniverseDomain.FORGE: "Forge CI/CD",
        UniverseDomain.MEMORA: "Memora Memory",
        UniverseDomain.INFERENCE: "Inference Gateway",
        UniverseDomain.INTELX: "IntelX Research",
        UniverseDomain.STRATEX: "Stratex Trading",
        UniverseDomain.INFRA: "Operational Infra",
    }

    for d in UniverseDomain:
        items = domain_map[d]
        d_risk = RiskLevel.NOMINAL
        if any(i.risk_level == RiskLevel.CRITICAL for i in items):
            d_risk = RiskLevel.CRITICAL
        elif any(i.risk_level == RiskLevel.HIGH for i in items):
            d_risk = RiskLevel.HIGH
        elif any(i.risk_level == RiskLevel.ELEVATED for i in items):
            d_risk = RiskLevel.ELEVATED

        domain_summaries.append(
            DomainPostureSummary(
                domain=d,
                display_name=display_names.get(d, d.value.title()),
                risk_level=d_risk,
                target_count=len(items),
                targets=items,
            )
        )

    # Compute overall posture
    overall_posture = RiskLevel.NOMINAL
    if any(r == RiskLevel.CRITICAL for r in all_risks):
        overall_posture = RiskLevel.CRITICAL
    elif any(r == RiskLevel.HIGH for r in all_risks):
        overall_posture = RiskLevel.HIGH
    elif any(r == RiskLevel.ELEVATED for r in all_risks):
        overall_posture = RiskLevel.ELEVATED

    critical_count = sum(1 for r in all_risks if r == RiskLevel.CRITICAL)
    high_count = sum(1 for r in all_risks if r == RiskLevel.HIGH)
    elevated_count = sum(1 for r in all_risks if r == RiskLevel.ELEVATED)

    health_score = max(0.0, 100.0 - (critical_count * 25.0 + high_count * 10.0 + elevated_count * 3.0))

    return UniverseMatrixResponse(
        ecosystem_health_score=round(health_score, 1),
        overall_posture=overall_posture,
        total_domains=len(UniverseDomain),
        total_active_targets=len(UNIVERSE_TARGETS),
        domains=domain_summaries,
        timestamp=datetime.now(UTC),
    )


@router.post(
    "/refresh-all",
    response_model=UniverseMatrixResponse,
    summary="Refresh Predictions Across All 9 FRIDAY Universe Domains",
)
async def refresh_universe_predictions(
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session),
) -> UniverseMatrixResponse:
    """Trigger recalculation across all 9 FRIDAY Universe pillars with fresh telemetry and IntelX context."""
    logger.info("refreshing_all_universe_predictions_started")
    await refresh_all_within_budget(session)
    return await get_universe_matrix(session=session)
