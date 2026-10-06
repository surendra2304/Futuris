"""Event emission and webhook dispatch with HMAC signing and retry backoff."""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from futuris.core.enums import ForecastEventType
from futuris.core.schemas import ForecastEvent
from futuris.infra.logging import get_logger
from futuris.infra.metrics import WEBHOOK_DELIVERY_TOTAL

logger = get_logger("futuris.events")

# Hostnames that must never be reachable by an outbound webhook (SSRF guard).
_BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".localhost", "localhost", ".home.arpa")
_BLOCKED_HOSTNAMES = {"metadata.google.internal", "169.254.169.254"}


class UnsafeWebhookUrlError(ValueError):
    """Raised when a webhook target is not a safe public HTTPS destination."""


def assert_public_destination(host: str) -> None:
    """Reject hosts that resolve to loopback/private/link-local/reserved addresses.

    A DNS failure is tolerated because the destination may be temporarily
    unresolvable; the check runs again immediately before every delivery.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            msg = f"webhook destination '{host}' resolves to non-public address {ip}"
            raise UnsafeWebhookUrlError(msg)


def assert_safe_webhook_url(url: str) -> None:
    """Validate a webhook URL: absolute HTTPS, public host, no credentials in URL."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        msg = "webhook URL must use https"
        raise UnsafeWebhookUrlError(msg)
    if parts.username or parts.password:
        msg = "webhook URL must not embed credentials"
        raise UnsafeWebhookUrlError(msg)
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        msg = "webhook URL must include a hostname"
        raise UnsafeWebhookUrlError(msg)
    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_HOST_SUFFIXES):
        msg = f"webhook destination '{host}' is not a public host"
        raise UnsafeWebhookUrlError(msg)
    assert_public_destination(host)


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

    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        max_attempts: int = 3,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.subscriptions: list[WebhookSubscription] = []
        self.custom_sinks: list[Callable[[ForecastEvent], Any]] = []
        self._client = http_client
        self.max_attempts = max(1, max_attempts)
        self.timeout_seconds = timeout_seconds
        self._delivery_stats = {
            "attempted": 0,
            "delivered": 0,
            "failed": 0,
            "rejected": 0,
        }

    def register_subscription(self, subscription: WebhookSubscription) -> None:
        """Register a new webhook subscription endpoint."""
        self.subscriptions.append(subscription)

    def add_sink(self, sink: Callable[[ForecastEvent], Any]) -> None:
        """Add custom callback sink."""
        self.custom_sinks.append(sink)

    def _resolve_client(self) -> httpx.AsyncClient:
        """Return the shared HTTP client, creating one on first use if needed."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_seconds)
        return self._client

    async def _deliver(self, sub: WebhookSubscription, event_dict: dict[str, Any]) -> bool:
        """Deliver one event to one subscription with bounded retries and backoff.

        Retries on timeouts, transport errors, 429 and 5xx. A 4xx (other than
        429) is a permanent rejection and is not retried.
        """
        try:
            assert_safe_webhook_url(sub.url)
        except UnsafeWebhookUrlError as exc:
            logger.warning("webhook_destination_rejected", url=sub.url, error=str(exc))
            WEBHOOK_DELIVERY_TOTAL.labels(status_code="rejected").inc()
            self._delivery_stats["rejected"] += 1
            return False

        self._delivery_stats["attempted"] += 1

        headers = {
            "Content-Type": "application/json",
            "X-Futuris-Signature": sign_payload(event_dict, sub.secret),
            "X-Futuris-Event-Type": str(event_dict.get("event_type", "")),
        }
        client = self._resolve_client()
        last_label = "no_attempt"
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = await client.post(
                    sub.url, json=event_dict, headers=headers, timeout=self.timeout_seconds
                )
                last_label = str(resp.status_code)
                if resp.is_success:
                    WEBHOOK_DELIVERY_TOTAL.labels(status_code=str(resp.status_code)).inc()
                    self._delivery_stats["delivered"] += 1
                    return True
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    break
            except Exception as exc:  # transport errors / timeouts
                last_label = "transport_error"
                logger.debug(
                    "webhook_delivery_attempt_failed",
                    url=sub.url,
                    attempt=attempt,
                    error=type(exc).__name__,
                )
            if attempt < self.max_attempts:
                await asyncio.sleep(0.05 * (2 ** (attempt - 1)))

        WEBHOOK_DELIVERY_TOTAL.labels(status_code=last_label).inc()
        self._delivery_stats["failed"] += 1
        logger.warning(
            "webhook_dispatch_failed",
            url=sub.url,
            attempts=self.max_attempts,
            last_status=last_label,
        )
        return False

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

        # 3. Webhook dispatch (concurrent, bounded by per-request timeout)
        event_dict = event.model_dump(mode="json")
        targets = [
            sub
            for sub in self.subscriptions
            if sub.is_active and event.event_type in sub.event_types
        ]
        if not targets:
            return

        results = await asyncio.gather(
            *(self._deliver(sub, event_dict) for sub in targets),
            return_exceptions=True,
        )
        for sub, result in zip(targets, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning("webhook_delivery_crashed", url=sub.url, error=type(result).__name__)
                WEBHOOK_DELIVERY_TOTAL.labels(status_code="error").inc()

    def delivery_stats(self) -> dict[str, int]:
        """Counters describing how outbound delivery has actually behaved."""
        return dict(self._delivery_stats)

    async def aclose(self) -> None:
        """Close the shared HTTP client if this emitter created one."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


event_emitter = EventEmitter()
