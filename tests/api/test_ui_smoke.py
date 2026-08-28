"""Smoke tests ensuring UI static distribution is correctly mounted and accessible via FastAPI."""

import httpx
import pytest

from futuris.api.app import app


@pytest.mark.asyncio
async def test_ui_dist_mount_accessibility():
    """Verify that /ui serves the compiled React dashboard index.html."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/ui/")
        assert resp.status_code == 200
        assert "Futuris | Predictive Intelligence Platform" in resp.text
        assert '<div id="root"></div>' in resp.text
