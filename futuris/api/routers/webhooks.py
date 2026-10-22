"""Inbound webhook router receiving research catalysts and external triggers."""

import hmac
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from futuris.infra.logging import get_logger
from futuris.integrations.memora_event_consumer import _persist_notice_to_memora
from futuris.storage.db import async_session_factory
from futuris.storage.models import IntelXNoticeModel
from futuris.storage.repositories import ForecastRepository

logger = get_logger("futuris.api.webhooks")

router = APIRouter(tags=["Webhooks and Research Catalysts"])


def verify_inbound_webhook_auth(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> None:
    """Authenticate inbound IntelX catalyst deliveries with a shared secret.

    Senders present the same convention used elsewhere in the fleet: an API key
    in ``X-API-Key`` or as a Bearer token. Configuration falls back from the
    dedicated ``INTELX_WEBHOOK_API_KEY`` to the FRIDAY/master credentials so a
    deployment already configured for the fleet keeps working. Authentication is
    fail-closed: an unconfigured deployment rejects deliveries with 503 rather
    than accepting unauthenticated writes.
    """
    from futuris.infra.config import settings

    # Settings only (B21): the process environment is not consulted directly.
    expected = (
        settings.INTELX_WEBHOOK_API_KEY
        or settings.FUTURIS_FRIDAY_API_KEY
        or settings.FUTURIS_API_KEY
    )
    if not expected or len(expected) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Inbound webhook authentication is not configured.",
        )

    supplied = x_api_key
    if not supplied and authorization:
        supplied = (
            authorization[len("Bearer ") :].strip()
            if authorization.startswith("Bearer ")
            else authorization.strip()
        )
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing inbound webhook credentials.",
        )


async def _forecast_repo_dependency():
    async with async_session_factory() as session:
        yield ForecastRepository(session)


class ResearchFindingEventData(BaseModel):
    run_id: str = Field(..., max_length=100, description="IntelX research run ID")
    finding_summary: str = Field(
        ..., min_length=1, max_length=8000, description="Key catalyst or finding text"
    )
    category: str = Field(
        default="market_moving",
        description="market_moving | regulatory_change | emerging_threat",
    )
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    domain: str = Field(default="market")
    recommended_forecast_targets: list[str] = Field(default_factory=list)
    timestamp: str | None = None


class IntelXResearchWebhookPayload(BaseModel):
    event: str = Field(default="research_finding_relevant")
    data: ResearchFindingEventData


_SUPPORTED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}


def _symbol_from_explicit_targets(targets: list[str]) -> str | None:
    """Map only explicit, supported market targets; never infer an asset from prose."""
    for target in targets:
        normalized = re.sub(r"[^A-Z0-9:._-]", "", target.strip().upper())
        if normalized in _SUPPORTED_SYMBOLS:
            return normalized
        parts = normalized.split(":")
        if len(parts) >= 3 and parts[:2] == ["MARKET", "CRYPTO"] and parts[2] in _SUPPORTED_SYMBOLS:
            return parts[2]
    return None


async def _store_webhook_notice(payload: IntelXResearchWebhookPayload) -> tuple[str, bool]:
    """Durably store a stable copy before returning an HTTP acknowledgement."""
    event_id = f"intelx-webhook:{payload.data.run_id}"[:128]
    notice_payload = {
        "event": payload.event,
        "run_id": payload.data.run_id,
        "finding_summary": payload.data.finding_summary,
        "category": payload.data.category,
        "confidence": payload.data.confidence,
        "domain": payload.data.domain,
        "recommended_forecast_targets": payload.data.recommended_forecast_targets,
        "timestamp": payload.data.timestamp,
        "futuris_processing": {"status": "received"},
    }
    memora_event = {
        "id": 0,
        "event_id": event_id,
        "event_type": "intelx.news",
    }
    try:
        await _persist_notice_to_memora(
            memora_event,
            {
                "headline": payload.data.finding_summary[:2000],
                "summary": payload.data.finding_summary,
                "source_agent": "intelx",
                "source_type": "web_scrape",
                "category": payload.data.category,
                "domain": payload.data.domain,
                "relevance": {
                    "category": payload.data.category,
                    "domain": payload.data.domain,
                    "confidence": payload.data.confidence,
                },
                "recommended_forecast_targets": payload.data.recommended_forecast_targets,
                "published_at": payload.data.timestamp,
            },
        )
    except Exception as exc:
        logger.warning("intelx_webhook_memora_store_failed", error=type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="IntelX event was not durably stored in Memora; retry with the same run_id.",
        ) from exc
    try:
        async with async_session_factory() as session:
            existing = await session.get(IntelXNoticeModel, event_id)
            if existing is not None:
                if existing.payload.get("finding_summary") != payload.data.finding_summary:
                    raise HTTPException(
                        status_code=409,
                        detail="This IntelX run_id was already stored with different content.",
                    )
                return event_id, False
            session.add(
                IntelXNoticeModel(
                    event_id=event_id,
                    source_agent="intelx",
                    payload=notice_payload,
                )
            )
            await session.commit()
    except IntegrityError:
        # Concurrent retries race on the primary key. The unique constraint is
        # the final idempotency guard; accept only if the winning row exists.
        async with async_session_factory() as session:
            existing = await session.get(IntelXNoticeModel, event_id)
            if existing is None:
                raise
            if existing.payload.get("finding_summary") != payload.data.finding_summary:
                raise HTTPException(
                    status_code=409,
                    detail="This IntelX run_id was already stored with different content.",
                ) from None
        return event_id, False
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("intelx_webhook_durable_store_failed", error=type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="IntelX event was not durably stored; retry with the same run_id.",
        ) from exc
    return event_id, True


async def _set_processing_status(event_id: str, state: dict[str, Any]) -> None:
    async with async_session_factory() as session:
        notice = await session.get(IntelXNoticeModel, event_id)
        if notice is None:
            raise RuntimeError("durable IntelX notice disappeared before processing")
        notice.payload = {**notice.payload, "futuris_processing": state}
        await session.commit()


@router.post(
    "/webhooks/research-finding-relevant",
    status_code=status.HTTP_200_OK,
    summary="IntelX Catalyst Webhook Handler (shared secret)",
    dependencies=[Depends(verify_inbound_webhook_auth)],
)
async def handle_intelx_research_catalyst(
    payload: IntelXResearchWebhookPayload,
    background_tasks: BackgroundTasks,
    forecast_repo: ForecastRepository = Depends(_forecast_repo_dependency),
) -> dict[str, Any]:
    """Durably accept IntelX catalysts and reforecast only explicit supported targets."""
    data = payload.data
    event_id, is_new = await _store_webhook_notice(payload)
    if not is_new:
        return {
            "status": "already_received",
            "event_id": event_id,
            "recalibration_scheduled": False,
            "received_at": datetime.now(UTC).isoformat(),
        }

    symbol = _symbol_from_explicit_targets(data.recommended_forecast_targets)
    logger.info(
        "intelx_catalyst_webhook_received",
        event_id=event_id,
        category=data.category,
        domain=data.domain,
        summary=data.finding_summary[:60],
        targets=data.recommended_forecast_targets,
        supported_symbol=symbol,
    )

    if symbol:
        await _set_processing_status(event_id, {"status": "queued", "symbol": symbol})

        async def _async_reforecast():
            try:
                from futuris.api.routers.market import _generate_market_prediction

                result = await _generate_market_prediction(
                    symbol=symbol,
                    horizon_hours=24,
                    include_intelx=True,
                    include_inference=True,
                    forecast_repo=forecast_repo,
                    intelx_catalyst=(event_id, data.finding_summary),
                )
            except Exception as exc:
                logger.warning(
                    "catalyst_reforecast_failed", event_id=event_id, error=type(exc).__name__
                )
                try:
                    await _set_processing_status(
                        event_id,
                        {
                            "status": "failed",
                            "symbol": symbol,
                            "error": type(exc).__name__,
                        },
                    )
                except Exception as persist_exc:
                    logger.warning(
                        "catalyst_processing_status_persist_failed",
                        event_id=event_id,
                        error=type(persist_exc).__name__,
                    )
                return

            await _set_processing_status(
                event_id,
                {
                    "status": "forecast_generated",
                    "forecast_id": result.forecast_id,
                    "symbol": symbol,
                },
            )

        background_tasks.add_task(_async_reforecast)
    else:
        await _set_processing_status(event_id, {"status": "stored_no_supported_target"})

    return {
        "status": "stored",
        "event_id": event_id,
        "event": payload.event,
        "category": data.category,
        "affected_symbol": symbol,
        "recalibration_scheduled": bool(symbol),
        "processing_status": "queued" if symbol else "stored_no_supported_target",
        "received_at": datetime.now(UTC).isoformat(),
    }
