"""Integration tests for NEXUS telemetry connector, FridayClient SDK, and SENTINEL adapter."""

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from futuris.api.app import app
from futuris.api.deps import get_db_session
from futuris.connectors.nexus import NexusConnector
from futuris.core.enums import ScenarioType, SignalClass
from futuris.integrations.friday_client import FridayClient
from futuris.integrations.sentinel_schema import (
    SENTINEL_DEMO_TARGET,
    SentinelSecurityEvent,
    translate_sentinel_event_to_observation,
)
from futuris.scenarios.spec import ScenarioSpec
from futuris.storage.models import Base


@pytest.mark.asyncio
async def test_nexus_connector_with_mock_transport():
    """Verify NexusConnector parses HTTP JSON telemetry into typed Observations."""
    start = datetime(2026, 8, 28, 10, 0, 0, tzinfo=UTC)
    end = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)

    mock_data = [
        {
            "observed_at": "2026-08-28T10:00:00Z",
            "source": "nexus:cluster_1",
            "series_id": "service:checkout:requests_per_minute",
            "value": 3450.0,
            "unit": "rpm",
            "tags": {"region": "us-east"},
        },
        {
            "observed_at": "2026-08-28T11:00:00Z",
            "source": "nexus:cluster_1",
            "series_id": "service:checkout:requests_per_minute",
            "value": 3600.0,
            "unit": "rpm",
            "tags": {"region": "us-east"},
        },
    ]

    async def _mock_handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" in request.headers
        assert request.headers["Authorization"] == "Bearer test_key_123"
        return httpx.Response(200, json=mock_data)

    transport = httpx.MockTransport(_mock_handler)
    connector = NexusConnector(
        base_url="http://nexus-mock.local",
        api_key="test_key_123",
        transport=transport,
    )

    observations = await connector.fetch(start, end)
    assert len(observations) == 2
    assert observations[0].series_id == "service:checkout:requests_per_minute"
    assert observations[0].value == 3450.0
    assert observations[1].value == 3600.0


def test_sentinel_event_schema_translation():
    """Verify SENTINEL security events are mapped into FUTURIS Observations."""
    event = SentinelSecurityEvent(
        event_id=uuid4(),
        event_type="brute_force_attack",
        asset_id="auth-gateway",
        severity="critical",
        timestamp=datetime(2026, 8, 28, 14, 0, 0, tzinfo=UTC),
        anomaly_count=15,
        metadata={"attacker_ips": ["192.0.2.1", "192.0.2.2"]},
    )

    obs = translate_sentinel_event_to_observation(event)

    assert obs.series_id == "sentinel:brute_force_attack:auth-gateway"
    assert obs.value == 150.0  # 15 * 10.0 (critical weight)
    assert obs.unit == "threat_index"
    assert obs.tags["signal_class"] == SignalClass.AGENT_OBSERVATION.value
    assert obs.tags["severity"] == "critical"
    assert SENTINEL_DEMO_TARGET == "sentinel:incident_escalation_24h"


@pytest.mark.asyncio
async def test_friday_client_sdk_integration():
    """Verify FridayClient typed SDK interacts with FastAPI app for forecasts and scenarios."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:

        async def _override_get_db():
            yield session

        app.dependency_overrides[get_db_session] = _override_get_db
        transport = httpx.ASGITransport(app=app)
        client = FridayClient(base_url="http://testserver", transport=transport)

        # 1. Request Forecast via SDK
        forecast = await client.request_forecast(
            target="service:checkout:capacity_exceedance_24h",
            horizon="24h",
        )
        assert forecast.forecast_id is not None
        assert forecast.target == "service:checkout:capacity_exceedance_24h"

        # 2. Compare Scenarios via SDK
        spec = ScenarioSpec(
            spec_id=uuid4(),
            name="Stress Scenario",
            scenario_type=ScenarioType.STRESS,
            assumption_overrides={"demand": 4500.0},
            rationale="Simulating surge",
        )
        comparison = await client.compare_scenarios(
            forecast_id=forecast.forecast_id,
            scenarios=[spec],
        )
        assert len(comparison.scenario_names) == 1
        assert "variable_matrix" in comparison.model_dump()

        # 3. Subscribe Webhook via SDK
        sub_res = await client.subscribe_webhook("https://example.com/friday-webhook")
        assert "secret" in sub_res

        app.dependency_overrides.clear()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
