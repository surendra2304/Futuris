"""Smoke test for health endpoint."""

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
