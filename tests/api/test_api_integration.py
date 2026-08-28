"""End-to-end API integration tests for forecasts, abstentions, scenarios, and webhooks."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from futuris.api.app import app
from futuris.api.deps import get_db_session
from futuris.storage.models import Base


@pytest_asyncio.fixture(scope="function")
async def api_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide isolated in-memory SQLite database for API tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def client(api_db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Provide httpx async client wired to test app with overridden DB session."""

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield api_db_session

    app.dependency_overrides[get_db_session] = _override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_full_forecast_lifecycle_api(client: httpx.AsyncClient):
    """Test full forecast lifecycle: create -> get -> manual resolution -> get outcome."""
    payload = {
        "target": "service:checkout:capacity_exceedance_24h",
        "horizon": "24h",
        "as_of": datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC).isoformat(),
    }
    resp = await client.post("/v1/forecasts", json=payload)
    assert resp.status_code == 201
    assert "X-Request-ID" in resp.headers
    data = resp.json()
    forecast_id = data["forecast_id"]
    assert data["target"] == payload["target"]
    assert data["prediction"] > 0
    assert data["status"] == "active"

    # 2. Get Forecast by ID
    get_resp = await client.get(f"/v1/forecasts/{forecast_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["forecast_id"] == forecast_id

    # 3. Manual Resolution
    res_payload = {
        "observed_value": 4120.0,
        "event_occurred": True,
        "note": "Spike verified during peak sale event",
    }
    res_resp = await client.post(
        f"/v1/forecasts/outcomes/{forecast_id}/resolve-manual", json=res_payload
    )
    assert res_resp.status_code == 200
    assert res_resp.json()["event_occurred"] is True

    # 4. Verify Outcome Endpoint
    out_resp = await client.get(f"/v1/forecasts/{forecast_id}/outcome")
    assert out_resp.status_code == 200
    assert out_resp.json()["observed_value"] == 4120.0


@pytest.mark.asyncio
async def test_forecast_abstention_gate(client: httpx.AsyncClient):
    """Test abstention path: required_confidence=high returns 202 abstained if not satisfied."""
    payload = {
        "target": "service:checkout:capacity_exceedance_24h",
        "horizon": "24h",
        "required_confidence": "high",
        "as_of": datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC).isoformat(),
    }
    resp = await client.post("/v1/forecasts", json=payload)
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "abstained"
    assert "does not satisfy required minimum threshold" in data["reason"]


@pytest.mark.asyncio
async def test_forecast_invalidation_api(client: httpx.AsyncClient):
    """Test forecast invalidation via explicit reason."""
    create_resp = await client.post(
        "/v1/forecasts",
        json={"target": "service:checkout:capacity_exceedance_24h", "horizon": "24h"},
    )
    f_id = create_resp.json()["forecast_id"]

    inv_resp = await client.post(
        f"/v1/forecasts/{f_id}/invalidate",
        json={"reason": "Cluster resized from 8 to 16 nodes"},
    )
    assert inv_resp.status_code == 200
    assert inv_resp.json()["status"] == "invalidated"


@pytest.mark.asyncio
async def test_scenarios_and_comparison_api(client: httpx.AsyncClient):
    """Test scenario simulation and side-by-side comparison endpoints."""
    create_resp = await client.post(
        "/v1/forecasts",
        json={"target": "service:checkout:capacity_exceedance_24h", "horizon": "24h"},
    )
    f_id = create_resp.json()["forecast_id"]

    scenario_payload = {
        "scenarios": [
            {
                "name": "Baseline",
                "scenario_type": "baseline",
                "assumption_overrides": {},
                "rationale": "Base",
            },
            {
                "name": "Stress",
                "scenario_type": "stress",
                "assumption_overrides": {"demand": 1.4},
                "rationale": "Stress",
            },
        ],
        "use_monte_carlo": False,
    }

    # 1. Run scenarios
    run_resp = await client.post(f"/v1/forecasts/{f_id}/scenarios", json=scenario_payload)
    assert run_resp.status_code == 200
    assert len(run_resp.json()) == 2

    # 2. Compare scenarios
    comp_resp = await client.post(f"/v1/forecasts/{f_id}/scenarios/compare", json=scenario_payload)
    assert comp_resp.status_code == 200
    comp_data = comp_resp.json()
    assert "variable_matrix" in comp_data
    assert "divergence_ranking" in comp_data


@pytest.mark.asyncio
async def test_webhook_subscription_and_deletion(client: httpx.AsyncClient):
    """Test webhook subscription registration delivering secret and subsequent deletion."""
    sub_payload = {
        "url": "https://example.com/webhook",
        "event_types": ["forecast_threshold_crossed", "forecast_invalidated"],
    }
    resp = await client.post("/v1/webhooks", json=sub_payload)
    assert resp.status_code == 201
    data = resp.json()
    assert "secret" in data
    assert data["secret"].startswith("whsec_")
    sub_id = data["subscription_id"]

    # Delete webhook
    del_resp = await client.delete(f"/v1/webhooks/{sub_id}")
    assert del_resp.status_code == 204


@pytest.mark.asyncio
async def test_error_envelope_consistency(client: httpx.AsyncClient):
    """Verify standard error envelope on 404 and 422 validation errors."""
    # 404 Not Found
    resp_404 = await client.get(f"/v1/forecasts/{uuid4()}")
    assert resp_404.status_code == 404
    err_404 = resp_404.json()
    assert "error" in err_404
    assert err_404["error"]["code"] == "not_found"

    # 422 Validation Error
    resp_422 = await client.post("/v1/forecasts", json={"invalid_field": 123})
    assert resp_422.status_code == 422
    err_422 = resp_422.json()
    assert err_422["error"]["code"] == "validation_error"
