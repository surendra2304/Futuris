"""Publish compact, signed Futuris forecast advisories to Memora's event feed."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from futuris.core.schemas import Forecast
from futuris.infra.config import settings
from futuris.infra.logging import get_logger

logger = get_logger("futuris.memora_forecast_publisher")
FORECAST_RECIPIENT = "sentinel"
FORECAST_INTENT = "futuris.forecast"


class MemoraForecastPublishError(RuntimeError):
    """Raised when a forecast advisory was not durably accepted by Memora."""


def build_forecast_envelope(
    forecast: Forecast,
    confidence_score: float,
    *,
    signing_key: str,
    created_at: float | None = None,
    recipient: str = FORECAST_RECIPIENT,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic-ID, compact advisory envelope from a real forecast."""
    if not signing_key:
        raise MemoraForecastPublishError(
            "FUTURIS_API_KEY is not configured for signed Memora events"
        )
    if not math.isfinite(confidence_score) or not 0.0 <= confidence_score <= 1.0:
        raise ValueError("confidence_score must be between zero and one")

    if forecast.status.value != "active":
        raise MemoraForecastPublishError("Only active forecasts may be published as advisories")
    if forecast.as_of.tzinfo is None or forecast.expires_at.tzinfo is None:
        raise MemoraForecastPublishError("Forecast timestamps must include a timezone")
    if forecast.as_of > datetime.now(UTC) or forecast.expires_at <= datetime.now(UTC):
        raise MemoraForecastPublishError("Forecast is not current and cannot be published")

    forecast_id = str(forecast.forecast_id)
    message_id = f"futuris-{forecast_id}"
    payload = {
        "forecast_id": forecast_id,
        "target": forecast.target,
        "status": forecast.status.value,
        "as_of": forecast.as_of.astimezone(UTC).isoformat(),
        "expires_at": forecast.expires_at.astimezone(UTC).isoformat(),
        "model_version": forecast.model_version,
        "prediction": forecast.prediction,
        "range_lower": forecast.range_lower,
        "range_upper": forecast.range_upper,
        "probability": forecast.probability,
        "confidence": confidence_score,
        "prediction_is_not_authorization": True,
    }
    envelope: dict[str, Any] = {
        "message_id": message_id,
        # An explicit correlation_id lets one journey thread the same ID across
        # hops (e.g. the IntelX signal that motivated this advisory).
        "correlation_id": correlation_id or forecast_id,
        "from_agent": "futuris",
        "to_agent": recipient.lower().strip(),
        "intent": FORECAST_INTENT,
        "priority": "normal",
        "ttl": 86400,
        "auth_token": None,
        "payload": payload,
        "created_at": created_at if created_at is not None else time.time(),
    }
    raw = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    envelope["signature"] = hmac.new(
        signing_key.encode("utf-8"), raw, hashlib.sha256
    ).hexdigest()
    return envelope


async def publish_forecast_advisory(
    forecast: Forecast,
    confidence_score: float,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    correlation_id: str | None = None,
) -> str:
    """Publish to Sentinel's Memora feed; return event ID only after acceptance."""
    key = settings.FUTURIS_API_KEY
    envelope = build_forecast_envelope(
        forecast,
        confidence_score,
        signing_key=key or "",
        recipient="all",
        correlation_id=correlation_id,
    )
    url = f"{settings.MEMORA_URL.rstrip('/')}/mesh/envelope"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    last_error = "unknown error"
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(transport=transport, timeout=3.0) as client:
                response = await client.post(url, json=envelope, headers=headers)
            if response.status_code in {200, 202}:
                receipt = response.json()
                if isinstance(receipt, dict) and receipt.get("status") in {"accepted", "duplicate"}:
                    return str(receipt.get("event_id", envelope["message_id"]))
                last_error = "Memora response did not confirm event acceptance"
            else:
                last_error = f"Memora returned HTTP {response.status_code}"
        except Exception as exc:
            last_error = type(exc).__name__
        logger.warning(
            "memora_forecast_publish_attempt_failed",
            event_id=envelope["message_id"],
            attempt=attempt + 1,
            error=last_error,
        )
    raise MemoraForecastPublishError(
        f"Memora did not durably accept forecast event {envelope['message_id']}: {last_error}"
    )
