"""Forecast management, abstention handling, lifecycle invalidation, and manual resolution."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from futuris.api.deps import (
    get_event_repo,
    get_forecast_repo,
    get_outcome_repo,
)
from futuris.core.engine import ForecastEngine
from futuris.core.enums import (
    ConfidenceLevel,
    ForecastEventType,
    ForecastStatus,
    ResolutionMethod,
)
from futuris.core.schemas import Driver, EvidenceRef, ForecastEvent, Outcome
from futuris.scenarios.spec import ScenarioSpec
from futuris.storage.repositories import (
    EventRepository,
    ForecastRepository,
    OutcomeRepository,
)

router = APIRouter(prefix="/v1/forecasts", tags=["Forecasts"])


def parse_horizon(horizon_str: str) -> timedelta:
    """Parse horizon strings like '24h', '30m', '7d' into timedeltas."""
    match = re.match(r"^(\d+)([mhd])$", horizon_str.lower().strip())
    if not match:
        return timedelta(hours=24)
    val, unit = int(match.group(1)), match.group(2)
    if unit == "m":
        return timedelta(minutes=val)
    if unit == "h":
        return timedelta(hours=val)
    if unit == "d":
        return timedelta(days=val)
    return timedelta(hours=24)


class ForecastCreateRequest(BaseModel):
    """Payload for requesting new operational forecast generation."""

    target: str = Field(
        ...,
        description="Target metric identifier, e.g. service:checkout:capacity_exceedance_24h",
    )
    horizon: str = Field(default="24h", description="Forecast horizon, e.g. '24h', '6h', '30m'")
    context: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    required_confidence: ConfidenceLevel | None = Field(
        default=None, description="Minimum acceptable confidence; abstains with 202 if not met"
    )
    scenario_set: list[ScenarioSpec] | None = None
    evidence_scope: str = "telemetry:synthetic"
    as_of: datetime | None = None


class RangeValues(BaseModel):
    lower: float
    central: float
    upper: float


class ForecastResponse(BaseModel):
    """Standard forecast response representation."""

    forecast_id: UUID
    target: str
    prediction: float
    range: RangeValues
    probability: float | None
    confidence: ConfidenceLevel
    drivers: list[Driver]
    evidence: list[EvidenceRef]
    assumptions: list[str]
    model: str
    expires_at: datetime
    review_at: datetime
    status: ForecastStatus


class ForecastAbstainedResponse(BaseModel):
    """Abstention response returned with 202 when engine cannot satisfy required confidence."""

    status: str = "abstained"
    reason: str
    target: str
    assessed_confidence: ConfidenceLevel
    required_confidence: ConfidenceLevel


class InvalidateRequest(BaseModel):
    """Required payload for manual forecast invalidation."""

    reason: str = Field(
        ..., min_length=3, description="Explicit rationale for invalidating forecast"
    )


class ManualResolveRequest(BaseModel):
    """Payload for manual ground truth resolution by human operator."""

    observed_value: float
    event_occurred: bool
    note: str = Field(..., min_length=3, description="Human resolution documentation note")


@router.post(
    "",
    response_model=ForecastResponse | ForecastAbstainedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create or Orchestrate a New Forecast",
)
async def create_forecast(
    req: ForecastCreateRequest,
    response: Response,
    forecast_repo: ForecastRepository = Depends(get_forecast_repo),
) -> Any:
    """Generate forecast and validate required confidence boundary."""
    engine = ForecastEngine()
    as_of = req.as_of or datetime.now(UTC)
    horizon_delta = parse_horizon(req.horizon)

    forecasts = await engine.orchestrate(
        target=req.target,
        as_of=as_of,
        horizon=horizon_delta,
        evidence_scope=req.evidence_scope,
    )
    f = forecasts[0]

    # Check Required Confidence Threshold (Abstention Gate)
    if req.required_confidence:
        order = {ConfidenceLevel.LOW: 1, ConfidenceLevel.MEDIUM: 2, ConfidenceLevel.HIGH: 3}
        if order[f.confidence] < order[req.required_confidence]:
            response.status_code = status.HTTP_202_ACCEPTED
            return ForecastAbstainedResponse(
                status="abstained",
                reason=(
                    f"Engine assessed confidence ({f.confidence.value}) does not satisfy "
                    f"required minimum threshold ({req.required_confidence.value})."
                ),
                target=req.target,
                assessed_confidence=f.confidence,
                required_confidence=req.required_confidence,
            )

    f.status = ForecastStatus.ACTIVE
    saved = await forecast_repo.create(f)

    return ForecastResponse(
        forecast_id=saved.forecast_id,
        target=saved.target,
        prediction=saved.prediction,
        range=RangeValues(
            lower=saved.range_lower, central=saved.prediction, upper=saved.range_upper
        ),
        probability=saved.probability,
        confidence=saved.confidence,
        drivers=saved.drivers,
        evidence=saved.evidence,
        assumptions=saved.assumptions,
        model=saved.model_version,
        expires_at=saved.expires_at,
        review_at=saved.review_at,
        status=saved.status,
    )


@router.get("", response_model=list[ForecastResponse], summary="List and Filter Forecasts")
async def list_forecasts(
    response: Response,
    target: str | None = Query(None),
    status: ForecastStatus | None = Query(None),
    as_of_after: datetime | None = Query(None),
    as_of_before: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    forecast_repo: ForecastRepository = Depends(get_forecast_repo),
) -> list[ForecastResponse]:
    """List forecasts with pagination and total count headers."""
    items = await forecast_repo.list_by_status(status) if status else []
    if not status:
        if target:
            items = await forecast_repo.list_by_target(target)
        else:
            items = await forecast_repo.list_by_status(ForecastStatus.ACTIVE)

    if as_of_after:
        items = [i for i in items if i.as_of >= as_of_after]
    if as_of_before:
        items = [i for i in items if i.as_of <= as_of_before]

    total_count = len(items)
    response.headers["X-Total-Count"] = str(total_count)
    response.headers["X-Limit"] = str(limit)
    response.headers["X-Offset"] = str(offset)

    sliced = items[offset : offset + limit]
    return [
        ForecastResponse(
            forecast_id=i.forecast_id,
            target=i.target,
            prediction=i.prediction,
            range=RangeValues(lower=i.range_lower, central=i.prediction, upper=i.range_upper),
            probability=i.probability,
            confidence=i.confidence,
            drivers=i.drivers,
            evidence=i.evidence,
            assumptions=i.assumptions,
            model=i.model_version,
            expires_at=i.expires_at,
            review_at=i.review_at,
            status=i.status,
        )
        for i in sliced
    ]


@router.get("/{forecast_id}", response_model=ForecastResponse, summary="Get Full Forecast Details")
async def get_forecast(
    forecast_id: UUID,
    forecast_repo: ForecastRepository = Depends(get_forecast_repo),
) -> ForecastResponse:
    """Retrieve full forecast aggregate root."""
    f = await forecast_repo.get(forecast_id)
    if not f:
        raise HTTPException(status_code=404, detail="Forecast not found")

    return ForecastResponse(
        forecast_id=f.forecast_id,
        target=f.target,
        prediction=f.prediction,
        range=RangeValues(lower=f.range_lower, central=f.prediction, upper=f.range_upper),
        probability=f.probability,
        confidence=f.confidence,
        drivers=f.drivers,
        evidence=f.evidence,
        assumptions=f.assumptions,
        model=f.model_version,
        expires_at=f.expires_at,
        review_at=f.review_at,
        status=f.status,
    )


@router.post(
    "/{forecast_id}/invalidate", response_model=ForecastResponse, summary="Invalidate Forecast"
)
async def invalidate_forecast(
    forecast_id: UUID,
    req: InvalidateRequest,
    forecast_repo: ForecastRepository = Depends(get_forecast_repo),
    event_repo: EventRepository = Depends(get_event_repo),
) -> ForecastResponse:
    """Invalidate active forecast with mandatory audit rationale."""
    f = await forecast_repo.get(forecast_id)
    if not f:
        raise HTTPException(status_code=404, detail="Forecast not found")

    updated = await forecast_repo.update_status(forecast_id, ForecastStatus.INVALIDATED)
    event = ForecastEvent(
        event_id=uuid4(),
        forecast_id=forecast_id,
        event_type=ForecastEventType.FORECAST_INVALIDATED,
        payload={"reason": req.reason, "target": f.target},
        emitted_at=datetime.now(UTC),
    )
    await event_repo.append(event)

    return ForecastResponse(
        forecast_id=updated.forecast_id,
        target=updated.target,
        prediction=updated.prediction,
        range=RangeValues(
            lower=updated.range_lower, central=updated.prediction, upper=updated.range_upper
        ),
        probability=updated.probability,
        confidence=updated.confidence,
        drivers=updated.drivers,
        evidence=updated.evidence,
        assumptions=updated.assumptions,
        model=updated.model_version,
        expires_at=updated.expires_at,
        review_at=updated.review_at,
        status=updated.status,
    )


@router.get("/{forecast_id}/outcome", response_model=Outcome, summary="Get Resolved Outcome")
async def get_forecast_outcome(
    forecast_id: UUID,
    outcome_repo: OutcomeRepository = Depends(get_outcome_repo),
) -> Outcome:
    """Retrieve ground-truth outcome resolution for forecast."""
    outcome = await outcome_repo.get_by_forecast(forecast_id)
    if not outcome:
        raise HTTPException(status_code=404, detail="Outcome not resolved yet for this forecast")
    return outcome


@router.post(
    "/{forecast_id}/resolve-manual",
    response_model=Outcome,
    summary="Manual Ground Truth Resolution",
)
@router.post(
    "/outcomes/{forecast_id}/resolve-manual",
    response_model=Outcome,
    summary="Manual Ground Truth Resolution",
)
async def resolve_manual(
    forecast_id: UUID,
    req: ManualResolveRequest,
    outcome_repo: OutcomeRepository = Depends(get_outcome_repo),
    forecast_repo: ForecastRepository = Depends(get_forecast_repo),
    event_repo: EventRepository = Depends(get_event_repo),
) -> Outcome:
    """Resolve ground truth manually by human operator."""
    f = await forecast_repo.get(forecast_id)
    if not f:
        raise HTTPException(status_code=404, detail="Forecast not found")

    outcome = Outcome(
        outcome_id=uuid4(),
        forecast_id=forecast_id,
        observed_value=req.observed_value,
        event_occurred=req.event_occurred,
        resolved_at=datetime.now(UTC),
        resolution_method=ResolutionMethod.HUMAN,
        ambiguity_note=req.note,
        resolution_rule_version="manual:human:v1",
    )
    saved = await outcome_repo.record_outcome(outcome)
    await forecast_repo.update_status(forecast_id, ForecastStatus.RESOLVED)

    event = ForecastEvent(
        event_id=uuid4(),
        forecast_id=forecast_id,
        event_type=ForecastEventType.FORECAST_OUTCOME_RECORDED,
        payload=saved.model_dump(mode="json"),
        emitted_at=datetime.now(UTC),
    )
    await event_repo.append(event)
    return saved
