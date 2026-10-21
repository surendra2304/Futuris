"""Tests for cross-agent forecast signaling and risk regime dispatching."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from futuris.api.app import app
from futuris.ecosystem.signals import SignalDispatcher, signal_dispatcher
from futuris.infra.resilience import CircuitState, peer_circuits


@pytest.mark.asyncio
async def test_notify_friday_forecast_success():
    """Verify SignalDispatcher sends formatted predictive notice to FRIDAY."""
    mock_response = httpx.Response(200, json={"status": "received", "broadcast_id": "abc"})

    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            assert "forecasts/broadcast" in str(request.url)
            assert request.headers.get("X-Caller-Agent") == "futuris"
            return mock_response

    dispatcher = SignalDispatcher(transport=MockTransport())
    breaker = peer_circuits.for_peer("FRIDAY")
    breaker.state = CircuitState.CLOSED

    ok = await dispatcher.notify_friday_forecast(
        {
            "target": "trading:BTCUSDT:price_variance_24h",
            "prediction": 65000.0,
            "probability": 0.88,
        }
    )
    assert ok is True


@pytest.mark.asyncio
async def test_notify_friday_forecast_circuit_breaker():
    """Verify SignalDispatcher short-circuits gracefully when FRIDAY circuit is OPEN."""
    dispatcher = SignalDispatcher()
    breaker = peer_circuits.for_peer("FRIDAY")
    original_state = breaker.state
    try:
        breaker.state = CircuitState.OPEN
        breaker.last_state_change = datetime.now(UTC).timestamp()

        ok = await dispatcher.notify_friday_forecast({"target": "test"})
        assert ok is False
    finally:
        breaker.state = original_state


@pytest.mark.asyncio
async def test_notify_stratex_risk_regime_success():
    """Verify SignalDispatcher dispatches calibrated tail-risk regime to Stratex."""
    mock_response = httpx.Response(201, json={"status": "regime_updated"})

    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            assert "signals/risk-regime" in str(request.url)
            return mock_response

    dispatcher = SignalDispatcher(transport=MockTransport())
    breaker = peer_circuits.for_peer("Stratex")
    breaker.state = CircuitState.CLOSED

    ok = await dispatcher.notify_stratex_risk_regime(
        target="BTCUSDT",
        risk_report={
            "basel_traffic_light": "yellow",
            "var_99": 0.052,
            "cvar_99": 0.078,
        },
    )
    assert ok is True


@pytest.mark.asyncio
async def test_notify_stratex_risk_regime_failure_graceful():
    """Verify SignalDispatcher handles connection error without raising exception."""

    class ErrorTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused by peer")

    dispatcher = SignalDispatcher(transport=ErrorTransport())
    breaker = peer_circuits.for_peer("Stratex")
    breaker.state = CircuitState.CLOSED

    ok = await dispatcher.notify_stratex_risk_regime(
        target="SOLUSDT",
        risk_report={"test": "data"},
    )
    assert ok is False


@pytest.mark.asyncio
async def test_ecosystem_signals_broadcast_endpoint():
    """Verify POST /v1/ecosystem/signals/broadcast endpoint dispatches to peers."""
    from futuris.infra.auth import AuthUser, require_admin

    app.dependency_overrides[require_admin] = lambda: AuthUser(
        label="test_admin", role="admin", principal_id="admin_1", tenant_id="t1", scopes=["*"]
    )
    try:
        with (
            patch.object(
                signal_dispatcher, "notify_friday_forecast", new_callable=AsyncMock
            ) as mock_f,
            patch.object(
                signal_dispatcher, "notify_stratex_risk_regime", new_callable=AsyncMock
            ) as mock_s,
        ):
            mock_f.return_value = True
            mock_s.return_value = True

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                req = {
                    "destination": "all",
                    "target": "trading:BTCUSDT:price_variance_24h",
                    "payload": {"prediction": 68000.0, "probability": 0.92},
                }
                resp = await client.post(
                    "/v1/ecosystem/signals/broadcast",
                    json=req,
                    headers={"X-API-Key": "test_admin_key"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "ok"
                assert data["delivered"]["FRIDAY"] is True
                assert data["delivered"]["Stratex"] is True
    finally:
        app.dependency_overrides.pop(require_admin, None)
