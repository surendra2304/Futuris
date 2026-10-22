"""Contract tests for advisory-only Futuris to Memora event publication."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from futuris.core.enums import ConfidenceLevel, ForecastStatus
from futuris.core.schemas import Forecast
from futuris.integrations.memora_forecast_publisher import (
    MemoraForecastPublishError,
    build_forecast_envelope,
    publish_forecast_advisory,
)


def _forecast(*, status: ForecastStatus = ForecastStatus.ACTIVE) -> Forecast:
    now = datetime.now(UTC)
    return Forecast(
        target="market:crypto:BTCUSDT:volatility_24h",
        as_of=now,
        horizon=timedelta(hours=24),
        expires_at=now + timedelta(hours=24),
        review_at=now + timedelta(hours=6),
        prediction=0.035,
        range_lower=-2.5,
        range_upper=3.5,
        probability=0.42,
        confidence=ConfidenceLevel.MEDIUM,
        model_version="statsforecast:market_volatility@v2",
        status=status,
    )


def test_envelope_is_signed_idempotent_and_advisory_only():
    forecast = _forecast()
    envelope = build_forecast_envelope(
        forecast,
        0.84,
        signing_key="futuris-test-key",
        created_at=1_800_000_000.0,
        recipient="all",
    )
    supplied_signature = envelope.pop("signature")
    raw = json.dumps(envelope, sort_keys=True, separators=(",", ":"), default=str).encode()

    assert hmac.compare_digest(
        supplied_signature,
        hmac.new(b"futuris-test-key", raw, hashlib.sha256).hexdigest(),
    )
    assert envelope["message_id"] == f"futuris-{forecast.forecast_id}"
    assert envelope["to_agent"] == "all"
    assert envelope["intent"] == "futuris.forecast"
    assert envelope["payload"]["prediction_is_not_authorization"] is True
    assert "executable_commands" not in envelope["payload"]


@pytest.mark.asyncio
async def test_publish_returns_id_only_after_memora_accepts(monkeypatch):
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            202,
            json={"status": "accepted", "event_id": "futuris-forecast-123", "cursor": 8},
        )

    monkeypatch.setattr(
        "futuris.integrations.memora_forecast_publisher.settings.FUTURIS_API_KEY",
        "futuris-test-key",
    )
    monkeypatch.setattr(
        "futuris.integrations.memora_forecast_publisher.settings.MEMORA_URL",
        "https://memora.invalid/",
    )

    result = await publish_forecast_advisory(
        _forecast(),
        0.84,
        transport=httpx.MockTransport(handler),
    )

    assert result == "futuris-forecast-123"
    assert captured["url"] == "https://memora.invalid/mesh/envelope"
    assert captured["authorization"] == "Bearer futuris-test-key"
    assert captured["payload"]["to_agent"] == "all"
    assert captured["payload"]["payload"]["prediction_is_not_authorization"] is True


@pytest.mark.asyncio
async def test_publish_fails_closed_when_memora_does_not_accept(monkeypatch):
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403, json={"detail": "sender denied"})

    monkeypatch.setattr(
        "futuris.integrations.memora_forecast_publisher.settings.FUTURIS_API_KEY",
        "futuris-test-key",
    )
    monkeypatch.setattr(
        "futuris.integrations.memora_forecast_publisher.settings.MEMORA_URL",
        "https://memora.invalid",
    )

    with pytest.raises(MemoraForecastPublishError, match="HTTP 403"):
        await publish_forecast_advisory(
            _forecast(),
            0.84,
            transport=httpx.MockTransport(handler),
        )

    assert calls == 2


def test_inactive_forecast_cannot_be_published():
    with pytest.raises(MemoraForecastPublishError, match="Only active forecasts"):
        build_forecast_envelope(
            _forecast(status=ForecastStatus.INVALIDATED),
            0.84,
            signing_key="futuris-test-key",
        )


def test_envelope_threads_explicit_correlation_id_for_one_journey():
    envelope = build_forecast_envelope(
        _forecast(),
        0.84,
        signing_key="futuris-test-key",
        correlation_id="corr-journey-intelx-1",
    )
    assert envelope["correlation_id"] == "corr-journey-intelx-1"
    assert envelope["payload"]["prediction_is_not_authorization"] is True
