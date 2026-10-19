"""Integration tests for /ready, /status, and standardized error envelopes."""

import pytest
from httpx import ASGITransport, AsyncClient

from futuris import __version__
from futuris.api.app import app


@pytest.mark.asyncio
async def test_ready_endpoint_healthy(health_storage):
    """GET /ready returns 200 OK and connected status when database is functional."""
    _ = health_storage
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["service"] == "futuris"
    assert data["database"] == "connected"
    assert data["version"] == __version__
    assert "observed_at" in data


@pytest.mark.asyncio
async def test_ready_endpoint_head_request(health_storage):
    """HEAD /ready returns 200 OK."""
    _ = health_storage
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.head("/ready")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_status_endpoint_returns_operational_telemetry(health_storage):
    """GET /status returns telemetry, uptime, memory, cache stats, and circuit breaker status."""
    _ = health_storage
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "futuris"
    assert data["port"] == 8000
    assert data["uptime_seconds"] >= 0
    assert "memory" in data
    assert "cache" in data
    assert "circuit_breakers" in data
    assert isinstance(data["circuit_breakers"], list)


@pytest.mark.asyncio
async def test_standardized_error_envelope_404():
    """Unmatched route returns standardized error envelope with message and success: false."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/nonexistent_endpoint_xyz")
    assert response.status_code == 404
    data = response.json()
    assert "error" in data
    assert data["success"] is False
    assert "message" in data


@pytest.mark.asyncio
async def test_standardized_error_envelope_422(monkeypatch):
    """Schema validation failure returns standardized error envelope with message
    and success: false.
    """
    from futuris.infra.config import settings

    monkeypatch.setattr(settings, "API_KEYS_ENABLED", False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/v1/forecasts", json={"target": ""})
    assert response.status_code == 422
    data = response.json()
    assert "error" in data
    assert data["success"] is False
    assert "message" in data



@pytest.mark.asyncio
async def test_request_id_header_is_bounded_and_sanitized(health_storage):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        valid = await client.get("/ready", headers={"X-Request-ID": "friday-task:123"})
        assert valid.headers["x-request-id"] == "friday-task:123"

        oversized = await client.get("/ready", headers={"X-Request-ID": "x" * 256})
        replacement = oversized.headers["x-request-id"]
        assert replacement != "x" * 256
        assert len(replacement) == 36
        assert all(
            char in "0123456789abcdef-" for char in replacement.lower()
        )
