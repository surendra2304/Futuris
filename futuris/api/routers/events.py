"""Audit event stream and HMAC webhook subscription router."""

from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from futuris.api.deps import get_event_emitter, get_event_repo
from futuris.core.enums import ForecastEventType
from futuris.core.schemas import ForecastEvent
from futuris.infra.events import EventEmitter, WebhookSubscription
from futuris.storage.repositories import EventRepository

router = APIRouter(prefix="/v1", tags=["Events & Webhooks"])


class WebhookCreateRequest(BaseModel):
    """Payload to register a new HMAC signed webhook subscription."""

    url: str = Field(..., description="Target HTTPS webhook endpoint")
    event_types: list[ForecastEventType] = Field(
        default_factory=lambda: [ForecastEventType.FORECAST_THRESHOLD_CROSSED]
    )


class WebhookCreatedResponse(BaseModel):
    """Returned ONCE upon webhook registration to deliver the HMAC secret."""

    subscription_id: UUID
    url: str
    event_types: list[ForecastEventType]
    secret: str = Field(..., description="HMAC SHA-256 secret key. Store securely; returned ONCE.")


@router.get("/events", response_model=list[ForecastEvent], summary="List Domain Events")
async def list_events(
    event_type: ForecastEventType | None = Query(None),
    since: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    event_repo: EventRepository = Depends(get_event_repo),
) -> list[ForecastEvent]:
    """List audit events with cursor/filter capabilities."""
    if event_type:
        events = await event_repo.list_by_type(event_type)
    else:
        events = []

    if since:
        events = [e for e in events if e.emitted_at >= since]

    return events[:limit]


@router.post(
    "/webhooks",
    response_model=WebhookCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Subscribe to Webhooks",
)
async def create_webhook(
    req: WebhookCreateRequest,
    emitter: EventEmitter = Depends(get_event_emitter),
) -> WebhookCreatedResponse:
    """Register webhook subscription and return HMAC-SHA256 signature secret ONCE."""
    sub_id = uuid4()
    secret = f"whsec_{uuid4().hex}"

    sub = WebhookSubscription(
        subscription_id=sub_id,
        url=req.url,
        event_types=req.event_types,
        secret=secret,
    )
    emitter.register_subscription(sub)

    return WebhookCreatedResponse(
        subscription_id=sub_id,
        url=req.url,
        event_types=req.event_types,
        secret=secret,
    )


@router.delete(
    "/webhooks/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Webhook",
)
async def delete_webhook(
    subscription_id: UUID,
    emitter: EventEmitter = Depends(get_event_emitter),
) -> None:
    """Remove a webhook subscription."""
    for sub in list(emitter.subscriptions):
        if sub.subscription_id == subscription_id:
            emitter.subscriptions.remove(sub)
            return

    raise HTTPException(status_code=404, detail="Webhook subscription not found")
