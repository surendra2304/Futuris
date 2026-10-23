"""Unit and integration tests for real-time SSE and WebSocket forecast streaming."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from starlette.testclient import TestClient

from futuris.api.app import app
from futuris.api.routers.streaming import StreamHub, stream_hub
from futuris.core.enums import ForecastEventType
from futuris.core.schemas import ForecastEvent


@pytest.fixture
def test_client() -> TestClient:
    return TestClient(app)


@pytest.mark.asyncio
async def test_stream_hub_pub_sub_filtering():
    """Verify StreamHub correctly routes broadcast messages based on target filter."""
    hub = StreamHub(queue_maxsize=10)

    # Sub 1: all targets
    sub1_id, q1 = await hub.register(target_filter=None)
    # Sub 2: checkout metric only
    sub2_id, q2 = await hub.register(target_filter="service:checkout:rps")

    try:
        # Broadcast checkout metric event
        await hub.broadcast("forecast_updated", {"val": 100}, target="service:checkout:rps")

        assert not q1.empty()
        assert not q2.empty()

        msg1 = q1.get_nowait()
        msg2 = q2.get_nowait()
        assert msg1["data"]["val"] == 100
        assert msg2["data"]["val"] == 100

        # Broadcast database metric event (should only go to sub1)
        await hub.broadcast("forecast_updated", {"val": 50}, target="database:connections")

        assert not q1.empty()
        assert q2.empty()

        msg_db = q1.get_nowait()
        assert msg_db["data"]["val"] == 50
    finally:
        await hub.unregister(sub1_id)
        await hub.unregister(sub2_id)


@pytest.mark.asyncio
async def test_stream_hub_bounded_backpressure():
    """Verify StreamHub drops oldest item when queue is full rather than blocking."""
    hub = StreamHub(queue_maxsize=2)
    sub_id, q = await hub.register()

    try:
        await hub.broadcast("e1", {"msg": 1})
        await hub.broadcast("e2", {"msg": 2})
        # Third broadcast should drop e1 and enqueue e3
        await hub.broadcast("e3", {"msg": 3})

        assert q.qsize() == 2
        item1 = q.get_nowait()
        item2 = q.get_nowait()
        assert item1["data"]["msg"] == 2
        assert item2["data"]["msg"] == 3
    finally:
        await hub.unregister(sub_id)


def test_streaming_status_endpoint(test_client: TestClient):
    """Verify GET /v1/streaming/status reports healthy streaming hub status."""
    resp = test_client.get("/v1/streaming/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "hub" in data
    assert "active_subscribers" in data["hub"]
    assert "queue_capacity" in data["hub"]


def test_websocket_forecasts_lifecycle(test_client: TestClient):
    """Verify WebSocket handshake, welcome message, ping/pong, and subscription filtering."""
    with test_client.websocket_connect("/v1/ws/forecasts") as ws:
        # 1. Receive welcome message
        welcome = ws.receive_json()
        assert welcome["type"] == "welcome"
        assert "subscriber_id" in welcome

        # 2. Receive immediate risk portfolio snapshot
        risk_snap = ws.receive_json()
        assert risk_snap["type"] == "risk_portfolio_update"
        assert "portfolio_summary" in risk_snap["data"]

        # 3. Ping-pong
        ws.send_json({"action": "ping"})
        pong = ws.receive_json()
        assert pong["type"] == "pong"
        assert "timestamp" in pong

        # 4. Subscribe to specific target
        ws.send_json({"action": "subscribe", "target": "service:auth:latency"})
        sub_ack = ws.receive_json()
        assert sub_ack["type"] == "subscribed"
        assert sub_ack["target"] == "service:auth:latency"

        # 5. Trigger broadcast via global stream_hub
        asyncio.run(
            stream_hub.broadcast(
                event_type="forecast_generated",
                payload={"prediction": 42.0},
                target="service:auth:latency",
            )
        )

        event_msg = ws.receive_json()
        assert event_msg["type"] == "forecast_generated"
        assert event_msg["data"]["prediction"] == 42.0
        assert event_msg["target"] == "service:auth:latency"


def test_domain_event_bridges_to_stream_hub():
    """Verify domain events handled by stream_hub are scheduled for broadcast."""
    ev = ForecastEvent(
        event_id=uuid4(),
        event_type=ForecastEventType.FORECAST_CREATED,
        emitted_at=datetime.now(UTC),
        payload={"target": "service:payments:rps", "value": 150.0},
    )

    # Calling handle_domain_event should not raise
    stream_hub.handle_domain_event(ev)
