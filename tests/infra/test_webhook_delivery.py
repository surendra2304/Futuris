"""Regression tests for outbound webhook delivery (bug C5: retries were dead code)."""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from futuris.core.enums import ForecastEventType
from futuris.core.schemas import ForecastEvent
from futuris.infra.events import EventEmitter, WebhookSubscription


def make_event() -> ForecastEvent:
    return ForecastEvent(
        event_id=uuid4(),
        forecast_id=uuid4(),
        event_type=ForecastEventType.FORECAST_CREATED,
        payload={"target": "service:checkout:capacity_exceedance_24h", "prediction": 1234.5},
        emitted_at=datetime.now(UTC),
    )


def make_subscription(url: str = "https://example.com/hook") -> WebhookSubscription:
    return WebhookSubscription(
        subscription_id=uuid4(),
        url=url,
        event_types=[ForecastEventType.FORECAST_CREATED],
        secret="whsec_test_secret",
    )


@pytest.mark.asyncio
async def test_webhook_is_retried_then_succeeds():
    """A 500 is retried; the third attempt's success is recorded as delivered."""
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(500, json={"error": "try again"})
        return httpx.Response(202, json={"ok": True})

    emitter = EventEmitter(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    emitter.register_subscription(make_subscription())
    await emitter.emit(make_event())
    await emitter.aclose()

    assert len(attempts) == 3


@pytest.mark.asyncio
async def test_webhook_retries_are_bounded():
    """A permanently failing endpoint is tried at most max_attempts times."""
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503, json={"error": "down"})

    emitter = EventEmitter(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), max_attempts=3
    )
    emitter.register_subscription(make_subscription())
    await emitter.emit(make_event())
    await emitter.aclose()

    assert len(attempts) == 3


@pytest.mark.asyncio
async def test_client_error_is_not_retried():
    """A 4xx (other than 429) is a permanent rejection and is attempted once."""
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(400, json={"error": "bad payload"})

    emitter = EventEmitter(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    emitter.register_subscription(make_subscription())
    await emitter.emit(make_event())
    await emitter.aclose()

    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_delivered_payload_is_hmac_signed():
    """The receiver must be able to verify the X-Futuris-Signature header."""
    captured: dict = {}

    def handler(_request: httpx.Request) -> httpx.Response:
        captured["body"] = _request.content
        captured["signature"] = _request.headers.get("X-Futuris-Signature")
        captured["event_type"] = _request.headers.get("X-Futuris-Event-Type")
        return httpx.Response(200)

    emitter = EventEmitter(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    sub = make_subscription()
    emitter.register_subscription(sub)
    event = make_event()
    await emitter.emit(event)
    await emitter.aclose()

    received = json.loads(captured["body"])
    assert received["event_id"] == str(event.event_id)
    assert captured["event_type"] == "forecast_created"

    expected = hmac.new(
        sub.secret.encode(),
        json.dumps(received, sort_keys=True, default=str).encode(),
        hashlib.sha256,
    ).hexdigest()
    assert captured["signature"] == expected


@pytest.mark.asyncio
async def test_internal_destination_is_rejected_without_a_request():
    """A subscription that resolves to a private host is refused before delivery."""
    requests_made: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        requests_made.append(1)
        return httpx.Response(200)

    emitter = EventEmitter(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    emitter.register_subscription(make_subscription(url="https://127.0.0.1/hook"))
    await emitter.emit(make_event())
    await emitter.aclose()

    assert requests_made == []


@pytest.mark.asyncio
async def test_unsupported_event_type_is_not_delivered():
    """Subscriptions only receive the event types they asked for."""
    requests_made: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        requests_made.append(1)
        return httpx.Response(200)

    emitter = EventEmitter(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    sub = make_subscription()
    sub.event_types = [ForecastEventType.MODEL_PROMOTED]
    emitter.register_subscription(sub)
    await emitter.emit(make_event())
    await emitter.aclose()

    assert requests_made == []
