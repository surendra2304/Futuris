"""Forecast management, abstention handling, lifecycle invalidation, and manual resolution."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator

from futuris.api.deps import (
    get_event_repo,
    get_forecast_repo,
    get_outcome_repo,
)
from futuris.core.engine import ForecastEngine
from futuris.core.enums import (
    ConfidenceLevel,
    EvidenceClass,
    ForecastEventType,
    ForecastStatus,
    ResolutionMethod,
)
from futuris.core.schemas import Driver, EvidenceRef, ForecastEvent, Outcome
from futuris.infra.audit import AuditLogger
from futuris.infra.auth import AllowAnonymousRead, RequireAdmin, RequireAnalyst
from futuris.scenarios.spec import ScenarioSpec
from futuris.storage.repositories import (
    EventRepository,
    ForecastRepository,
    OutcomeRepository,
)
from futuris.upgrade.compat import _confidence_to_float
from futuris.upgrade.models import ForecastEnvelope
from futuris.upgrade.quality import ForecastQualityGate

router = APIRouter(prefix="/v1/forecasts", tags=["Forecasts"])


MIN_HORIZON = timedelta(minutes=1)
MAX_HORIZON = timedelta(days=365)


def parse_horizon(horizon_str: str) -> timedelta:
    """Parse horizon strings like '24h', '30m', '7d' into timedeltas.

    Bounded on purpose: an unbounded ``timedelta(days=n)`` overflows the C
    integer type for values like ``99999999999999999999d``, which used to
    surface as an opaque 500 (and would produce a nonsensical expiry date even
    if it did fit).
    """
    match = re.match(r"^(\d+)([mhd])$", horizon_str.lower().strip())
    if not match:
        raise ValueError(
            f"Invalid horizon '{horizon_str}'. Use <number><m|h|d>, e.g. '30m', '24h', '7d'."
        )
    val, unit = int(match.group(1)), match.group(2)
    try:
        delta = {
            "m": timedelta(minutes=val),
            "h": timedelta(hours=val),
            "d": timedelta(days=val),
        }[unit]
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"Horizon '{horizon_str}' is too large to represent.") from exc
    if delta < MIN_HORIZON or delta > MAX_HORIZON:
        raise ValueError(
            f"Horizon '{horizon_str}' is outside the supported range "
            f"(1 minute to 365 days)."
        )
    return delta


class ForecastCreateRequest(BaseModel):
    """Payload for requesting new operational forecast generation."""

    target: str = Field(
        ...,
        description="Target metric identifier, e.g. service:checkout:capacity_exceedance_24h",
    )
    horizon: str = Field(default="24h", description="Forecast horizon, e.g. '24h', '6h', '30m'")

    @field_validator("horizon")
    @classmethod
    def _validate_horizon(cls, value: str) -> str:
        parse_horizon(value)
        return value

    @field_validator("as_of")
    @classmethod
    def _validate_as_of(cls, value: datetime | None) -> datetime | None:
        """Refuse timestamps the engine cannot honestly forecast from.

        A far-future ``as_of`` used to reach pandas arithmetic and surface as
        ``'datetime.datetime' object has no attribute 'floor'``; a far-past one
        asks the engine to predict from stale data. Both are caller errors and
        both answer 422 with the acceptable window.
        """
        if value is None:
            return None
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        if moment > now + timedelta(minutes=5):
            raise ValueError(
                "as_of is in the future; forecasts must be anchored to a time that "
                "has already happened (at most 5 minutes of clock skew is tolerated)."
            )
        if moment < now - timedelta(days=365):
            raise ValueError("as_of is more than a year old; the engine has no data for it.")
        return value
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
    as_of: datetime
    horizon: str = "24h"
    expires_at: datetime
    review_at: datetime
    status: ForecastStatus
    created_at: datetime | None = None
    evidence_class: EvidenceClass = EvidenceClass.SYNTHETIC
    evidence_source: str | None = None
    prediction_is_not_authorization: bool = Field(
        default=True,
        description="Architectural invariant: a forecast never grants execution authority.",
    )
    executable_commands: list[str] = Field(
        default_factory=list,
        description="Always empty by construction; Futuris never emits runnable commands.",
    )
    model_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Model family, validation score and selection provenance. "
            "selection_degraded=true means the candidate search was bounded by "
            "CPU pressure or a request budget, and says which candidates were skipped."
        ),
    )


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
    user: RequireAnalyst,
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

    # Validate through quality gate
    gate = ForecastQualityGate()
    env = ForecastEnvelope(
        forecast_id=f.forecast_id,
        target=f.target,
        as_of=f.as_of,
        prediction=float(f.prediction),
        lower=float(f.range_lower),
        upper=float(f.range_upper),
        probability=float(f.probability) if f.probability is not None else None,
        confidence=_confidence_to_float(f.confidence),
        model_version=str(f.model_version),
        evidence_ids=[str(e.evidence_id) for e in f.evidence],
        assumptions=list(f.assumptions),
        source="forecast_api",
    )
    gate.require(env)

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

    await AuditLogger(forecast_repo.session).log_mutation(
        actor_label=user.label,
        action="create_forecast",
        entity="forecast",
        entity_id=str(saved.forecast_id),
        payload={
            "target": saved.target,
            "prediction": saved.prediction,
            "evidence_class": saved.evidence_class.value,
        },
    )

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
        as_of=saved.as_of,
        horizon=str(saved.horizon),
        expires_at=saved.expires_at,
        review_at=saved.review_at,
        status=saved.status,
        created_at=saved.as_of,
        evidence_class=saved.evidence_class,
        evidence_source=saved.evidence_source,
        model_metadata=saved.model_metadata,
        prediction_is_not_authorization=saved.prediction_is_not_authorization,
        executable_commands=saved.executable_commands,
    )


@router.get("", response_model=list[ForecastResponse], summary="List and Filter Forecasts")
async def list_forecasts(
    response: Response,
    user: AllowAnonymousRead,
    target: str | None = Query(None),
    status: ForecastStatus | None = Query(None),
    as_of_after: datetime | None = Query(None),
    as_of_before: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    forecast_repo: ForecastRepository = Depends(get_forecast_repo),
) -> list[ForecastResponse]:
    """List forecasts with pagination and total count headers."""
    _ = user
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
            as_of=i.as_of,
            horizon=str(i.horizon),
            expires_at=i.expires_at,
            review_at=i.review_at,
            status=i.status,
            created_at=i.as_of,
            evidence_class=i.evidence_class,
            evidence_source=i.evidence_source,
            model_metadata=i.model_metadata,
            prediction_is_not_authorization=i.prediction_is_not_authorization,
            executable_commands=i.executable_commands,
        )
        for i in sliced
    ]


@router.get("/{forecast_id}", response_model=ForecastResponse, summary="Get Full Forecast Details")
async def get_forecast(
    forecast_id: UUID,
    user: AllowAnonymousRead,
    forecast_repo: ForecastRepository = Depends(get_forecast_repo),
) -> ForecastResponse:
    """Retrieve full forecast aggregate root."""
    _ = user
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
        as_of=f.as_of,
        horizon=str(f.horizon),
        expires_at=f.expires_at,
        review_at=f.review_at,
        status=f.status,
        created_at=f.as_of,
        evidence_class=f.evidence_class,
        evidence_source=f.evidence_source,
        model_metadata=f.model_metadata,
        prediction_is_not_authorization=f.prediction_is_not_authorization,
        executable_commands=f.executable_commands,
    )


@router.post(
    "/{forecast_id}/invalidate", response_model=ForecastResponse, summary="Invalidate Forecast"
)
async def invalidate_forecast(
    forecast_id: UUID,
    req: InvalidateRequest,
    user: RequireAdmin,
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

    await AuditLogger(forecast_repo.session).log_mutation(
        actor_label=user.label,
        action="invalidate_forecast",
        entity="forecast",
        entity_id=str(forecast_id),
        payload={"reason": req.reason, "previous_status": f.status.value},
    )

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
        as_of=updated.as_of,
        horizon=str(updated.horizon),
        expires_at=updated.expires_at,
        review_at=updated.review_at,
        status=updated.status,
        created_at=updated.as_of,
        evidence_class=updated.evidence_class,
        evidence_source=updated.evidence_source,
        model_metadata=updated.model_metadata,
        prediction_is_not_authorization=updated.prediction_is_not_authorization,
        executable_commands=updated.executable_commands,
    )


@router.get("/{forecast_id}/outcome", response_model=Outcome, summary="Get Resolved Outcome")
async def get_forecast_outcome(
    forecast_id: UUID,
    user: AllowAnonymousRead,
    outcome_repo: OutcomeRepository = Depends(get_outcome_repo),
) -> Outcome:
    """Retrieve ground-truth outcome resolution for forecast."""
    _ = user
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
    user: RequireAdmin,
    outcome_repo: OutcomeRepository = Depends(get_outcome_repo),
    forecast_repo: ForecastRepository = Depends(get_forecast_repo),
    event_repo: EventRepository = Depends(get_event_repo),
) -> Outcome:
    """Resolve ground truth manually by human operator."""
    f = await forecast_repo.get(forecast_id)
    if not f:
        raise HTTPException(status_code=404, detail="Forecast not found")

    existing = await outcome_repo.get_by_forecast(forecast_id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Forecast {forecast_id} already has an outcome recorded at "
                f"{existing.resolved_at.isoformat()}; outcomes are immutable."
            ),
        )

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

    await AuditLogger(forecast_repo.session).log_mutation(
        actor_label=user.label,
        action="resolve_forecast_manual",
        entity="outcome",
        entity_id=str(saved.outcome_id),
        payload={
            "forecast_id": str(forecast_id),
            "observed_value": saved.observed_value,
            "event_occurred": saved.event_occurred,
            "note": req.note,
        },
    )
    return saved
