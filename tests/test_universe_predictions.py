"""Tests for FRIDAY Universe universal predictions and matrix API."""

import pytest
from httpx import ASGITransport, AsyncClient

from futuris.api.app import app
from futuris.core.universe_domains import UNIVERSE_TARGETS, RiskLevel, UniverseDomain


@pytest.mark.asyncio
async def test_universe_matrix_endpoint():
    """Verify GET /v1/predictions/matrix returns health score and all 9 domains."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/v1/predictions/matrix")
        assert resp.status_code == 200
        data = resp.json()

        assert "ecosystem_health_score" in data
        assert "overall_posture" in data
        assert data["total_domains"] == 9
        assert len(data["domains"]) == 9

        domain_names = {d["domain"] for d in data["domains"]}
        assert domain_names == {
            "friday",
            "sentinel",
            "cortex",
            "forge",
            "memora",
            "inference",
            "intelx",
            "stratex",
            "infra",
        }


@pytest.mark.asyncio
async def test_universal_prediction_request_sentinel():
    """Verify POST /v1/predictions/predict for Sentinel security target."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        payload = {
            "target": "sentinel:security:threat_anomaly_risk_24h",
            "context": {"probability": 0.15},
        }
        resp = await client.post("/v1/predictions/predict", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["target"] == "sentinel:security:threat_anomaly_risk_24h"
        assert data["domain"] == "sentinel"
        assert data["unit"] == "%"
        assert "mitigation_action" in data
        assert "risk_level" in data
        assert data["confidence"] in ["HIGH", "MEDIUM", "LOW"]


@pytest.mark.asyncio
async def test_universal_prediction_request_memora():
    """Verify POST /v1/predictions/predict for Memora storage horizon."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        payload = {
            "target": "memora:storage:capacity_exhaustion_days",
            "context": {"point_estimate": 45.0},
        }
        resp = await client.post("/v1/predictions/predict", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["domain"] == "memora"
        assert data["unit"] == "days"
        assert data["point_prediction"] == 45.0
