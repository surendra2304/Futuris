"""Integration tests for VyralX ecosystem adapter and connector factory."""

import httpx
import pytest

from futuris.connectors.factory import build_scheduler_connector
from futuris.connectors.vyralx import VyralXConnector
from futuris.ecosystem.adapters import EcosystemAdapter
from futuris.infra.config import settings


@pytest.mark.asyncio
async def test_scheduler_connector_factory_vyralx(monkeypatch):
    """Verify build_scheduler_connector returns VyralXConnector when configured."""
    monkeypatch.setattr(settings, "FUTURIS_TELEMETRY_SOURCE", "vyralx")
    connector = build_scheduler_connector()
    assert isinstance(connector, VyralXConnector)
    assert connector.base_url == settings.VYRALX_URL.rstrip("/")


@pytest.mark.asyncio
async def test_ecosystem_probe_peers_includes_vyralx():
    """Verify EcosystemAdapter includes VyralX in the 10-agent peer mesh probe."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "healthy", "service": "vyralx"})

    transport = httpx.MockTransport(handler)
    adapter = EcosystemAdapter(transport=transport)

    peers = await adapter.probe_peers()
    names = [p["name"] for p in peers]
    assert "VyralX" in names

    vyralx_entry = next(p for p in peers if p["name"] == "VyralX")
    assert vyralx_entry["role"] == "Social Trend Diffusion & Viral Cascade Engine"
    assert "Social Virality" in vyralx_entry["capabilities"]


@pytest.mark.asyncio
async def test_fetch_vyralx_trend_context():
    """Verify fetch_vyralx_trend_context delegates to VyralXConnector."""
    adapter = EcosystemAdapter()
    context = await adapter.fetch_vyralx_trend_context("ai_breakthrough")
    assert context is not None
    assert context["topic"] == "ai_breakthrough"
    assert "branching_ratio" in context
