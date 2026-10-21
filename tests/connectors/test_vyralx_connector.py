"""Unit tests for VyralXConnector."""

import time
from datetime import UTC, datetime

import httpx
import pytest

from futuris.connectors.vyralx import VyralXConnector
from futuris.infra.resilience import CircuitState, peer_circuits


@pytest.mark.asyncio
async def test_vyralx_connector_fetch_success():
    """Verify successful telemetry retrieval parses into valid Observation records."""
    start = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
    end = datetime(2026, 10, 20, 2, 0, tzinfo=UTC)
    series_id = "vyralx:trend:ai_agents"

    payload = {
        "data": [
            {
                "observed_at": "2026-10-20T00:00:00Z",
                "value": 45.2,
                "unit": "virality_score",
                "tags": {"sentiment": "positive"},
            },
            {
                "observed_at": "2026-10-20T01:00:00Z",
                "value": 58.7,
                "unit": "virality_score",
                "tags": {"sentiment": "very_positive"},
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" in request.headers
        assert request.headers["X-Caller-Agent"] == "futuris"
        assert request.url.path == "/v1/trends/telemetry"
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    connector = VyralXConnector(base_url="http://mock-vyralx:8000", transport=transport)

    observations = await connector.fetch(start, end, series_id=series_id)

    assert len(observations) == 2
    assert observations[0].series_id == series_id
    assert observations[0].value == 45.2
    assert observations[1].value == 58.7
    assert observations[0].source == "vyralx:social_mesh"


@pytest.mark.asyncio
async def test_vyralx_connector_degrades_on_http_error():
    """Verify fallback observations are generated smoothly when HTTP fails."""
    start = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
    end = datetime(2026, 10, 20, 1, 0, tzinfo=UTC)
    series_id = "vyralx:trend:fallback_test"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal error"})

    transport = httpx.MockTransport(handler)
    connector = VyralXConnector(base_url="http://mock-vyralx:8000", transport=transport)

    observations = await connector.fetch(start, end, series_id=series_id)

    assert len(observations) > 0
    assert observations[0].source == "vyralx:fallback"
    assert observations[0].tags.get("degraded") == "circuit_open_or_offline"
    assert all(obs.value > 0 for obs in observations)


@pytest.mark.asyncio
async def test_vyralx_connector_circuit_open_immediate_fallback():
    """Verify circuit breaker open state avoids network call and generates fallback."""
    circuit = peer_circuits.get("vyralx")
    circuit.state = CircuitState.OPEN
    circuit.opened_at = time.monotonic()

    try:
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, json={"data": []})

        transport = httpx.MockTransport(handler)
        connector = VyralXConnector(base_url="http://mock-vyralx:8000", transport=transport)

        start = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        end = datetime(2026, 10, 20, 0, 30, tzinfo=UTC)
        obs = await connector.fetch(start, end)

        assert not called
        assert len(obs) > 0
        assert obs[0].source == "vyralx:fallback"
    finally:
        circuit.reset()


@pytest.mark.asyncio
async def test_vyralx_fetch_viral_cascades_and_sentiment():
    """Verify cascade metrics and sentiment inflections queries and fallbacks."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/trends/cascades" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "topic": "autonomous_ai",
                    "status": "active",
                    "branching_ratio": 1.45,
                    "projected_peak_hours": 8.0,
                    "diffusion_velocity": 120.5,
                },
            )
        if "/v1/trends/sentiment" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "inflections": [
                        {
                            "timestamp": "2026-10-20T05:00:00Z",
                            "direction": "bullish_accel",
                            "magnitude": 0.82,
                        }
                    ]
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    connector = VyralXConnector(base_url="http://mock-vyralx:8000", transport=transport)

    cascades = await connector.fetch_viral_cascades("autonomous_ai", horizon_hours=12)
    assert cascades["branching_ratio"] == 1.45
    assert cascades["status"] == "active"

    sentiment = await connector.fetch_sentiment_inflections("autonomous_ai")
    assert len(sentiment) == 1
    assert sentiment[0]["direction"] == "bullish_accel"
