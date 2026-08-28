"""Event emission and webhook dispatch with HMAC signing and retry backoff."""

import asyncio
import hashlib
import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx

from futuris.core.enums import ForecastEventType
from futuris.core.schemas import ForecastEvent
from futuris.infra.logging import get_logger

logger = get_logger("futuris.events")


@dataclass
class WebhookSubscription:
    """Registered webhook subscription endpoint."""

    subscription_id: UUID
    url: str
    event_types: list[ForecastEventType]
    secret: str
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def sign_payload(payload: dict[str, Any], secret: str) -> str:
    """Generate SHA-256 HMAC signature for JSON webhook payload."""
    serialized = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), serialized, hashlib.sha256).hexdigest()


class EventEmitter:
    """Dispatches domain lifecycle events to registered in-process sinks and webhooks."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self.subscriptions: list[WebhookSubscription] = []
        self.custom_sinks: list[Callable[[ForecastEvent], Any]] = []
        self._client = http_client

    def register_subscription(self, subscription: WebhookSubscription) -> None:
        """Register a new webhook subscription endpoint."""
        self.subscriptions.append(subscription)

    def add_sink(self, sink: Callable[[ForecastEvent], Any]) -> None:
        """Add custom callback sink."""
        self.custom_sinks.append(sink)

    async def emit(self, event: ForecastEvent) -> None:
        """Emit event to log sink, custom in-process sinks, and active webhooks."""
        # 1. Log sink
        logger.info(
            "domain_event_emitted",
            event_id=str(event.event_id),
            event_type=event.event_type.value,
            forecast_id=str(event.forecast_id) if event.forecast_id else None,
        )

        # 2. Custom callbacks
        for sink in self.custom_sinks:
            try:
                res = sink(event)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.error("sink_dispatch_error", error=str(e))

        # 3. Webhook dispatch
        event_dict = event.model_dump(mode="json")
        for sub in self.subscriptions:
            if not sub.is_active or event.event_type not in sub.event_types:
                continue

            signature = sign_payload(event_dict, sub.secret)
            headers = {
                "Content-Type": "application/json",
                "X-Futuris-Signature": signature,
                "X-Futuris-Event-Type": event.event_type.value,
            }

            # Retry with exponential backoff (max 3 attempts)
            for attempt in range(1, 4):
                try:
                    if self._client:
                        resp = await self._client.post(
                            sub.url,
                            json=event_dict,
                            headers=headers,
                            timeout=5.0,
                        )
                        if resp.is_success:
                            break
                    break
                except Exception as ex:
                    if attempt == 3:
                        logger.warning(
                            "webhook_dispatch_failed",
                            url=sub.url,
                            attempt=attempt,
                            error=str(ex),
                        )
                    else:
                        await asyncio.sleep(0.01 * (2 ** (attempt - 1)))


event_emitter = EventEmitter()
