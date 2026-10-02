"""Smoke test for health endpoint."""

from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from futuris import __version__
from futuris.api.app import app


@pytest.mark.asyncio
async def test_health_check():
    """Verify GET /health returns status ok and correct version."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == __version__


@pytest.mark.asyncio
async def test_health_declares_evidence_class_and_observation_time():
    """The answer must name the class of evidence it carries and when it observed.

    A process that answers /health has proven it is up, not that any dependency
    is reachable, so the payload has to say which of those two it is.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["evidence_class"] == "process_liveness"
    assert datetime.fromisoformat(data["observed_at"]).tzinfo is not None


@pytest.mark.asyncio
async def test_health_answers_head_request():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.head("/health")
    assert response.status_code == 200
