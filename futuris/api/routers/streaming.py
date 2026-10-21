"""Real-time Server-Sent Events (SSE) and WebSocket pub/sub streaming for forecast telemetry."""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from futuris.core.schemas import ForecastEvent
from futuris.infra.events import event_emitter
from futuris.infra.logging import get_logger

logger = get_logger("futuris.api.streaming")

router = APIRouter(prefix="/v1", tags=["Real-Time Streaming"])


class StreamHub:
    """In-memory pub/sub broadcast hub with bounded backpressure queues."""

    def __init__(self, queue_maxsize: int = 100) -> None:
        self.queue_maxsize = queue_maxsize
        # subscriber_id -> (queue, target_filter)
        self._subscribers: dict[str, tuple[asyncio.Queue[dict[str, Any]], str | None]] = {}
        self._lock = asyncio.Lock()
        self.total_broadcasts: int = 0

    async def register(
        self, target_filter: str | None = None
    ) -> tuple[str, asyncio.Queue[dict[str, Any]]]:
        """Register a new consumer queue with an optional target metric filter."""
        sub_id = str(uuid4())
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.queue_maxsize)
        async with self._lock:
            self._subscribers[sub_id] = (q, target_filter)
        logger.debug("stream_subscriber_registered", subscriber_id=sub_id, target=target_filter)
        return sub_id, q

    async def unregister(self, subscriber_id: str) -> None:
        """Unregister consumer and release queue resources."""
        async with self._lock:
            self._subscribers.pop(subscriber_id, None)
        logger.debug("stream_subscriber_unregistered", subscriber_id=subscriber_id)

    async def update_filter(self, subscriber_id: str, new_target: str | None) -> None:
        """Update target filter for an existing subscriber."""
        async with self._lock:
            if subscriber_id in self._subscribers:
                q, _ = self._subscribers[subscriber_id]
                self._subscribers[subscriber_id] = (q, new_target)

    async def broadcast(
        self,
        event_type: str,
        payload: dict[str, Any],
        target: str | None = None,
    ) -> int:
        """Broadcast an event payload to matching subscribers with bounded backpressure."""
        self.total_broadcasts += 1
        msg = {
            "type": event_type,
            "target": target,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": payload,
        }

        delivered = 0
        async with self._lock:
            active_items = list(self._subscribers.items())

        for sub_id, (queue, target_filter) in active_items:
            # If target_filter is set, only deliver if target matches
            if target_filter and target and target_filter != target:
                continue

            try:
                if queue.full():
                    with contextlib.suppress(asyncio.QueueEmpty):
                        queue.get_nowait()
                queue.put_nowait(msg)
                delivered += 1
            except Exception as e:
                logger.warning("broadcast_queue_error", subscriber_id=sub_id, error=str(e))

        return delivered

    def handle_domain_event(self, event: ForecastEvent) -> None:
        """Callback sink registered with event_emitter to bridge domain events to stream."""
        event_dict = event.model_dump(mode="json")
        target = event.payload.get("target") if isinstance(event.payload, dict) else None
        event_type_str = str(event.event_type.value)

        # Broadcast asynchronously without blocking emitter
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.broadcast(event_type_str, event_dict, target=target))
        except RuntimeError:
            pass

    def stats(self) -> dict[str, Any]:
        """Report stream hub telemetry."""
        return {
            "active_subscribers": len(self._subscribers),
            "total_broadcasts": self.total_broadcasts,
            "queue_capacity": self.queue_maxsize,
        }


# Global stream hub instance
stream_hub = StreamHub()
# Hook stream_hub to domain event emitter
event_emitter.add_sink(stream_hub.handle_domain_event)


@router.get("/streaming/events", summary="Server-Sent Events forecast updates stream")
@router.get("/forecasts/stream", summary="Alias for SSE forecast stream", include_in_schema=False)
async def sse_forecast_events(
    request: Request,
    target: str | None = Query(None, description="Optional target metric filter"),
) -> StreamingResponse:
    """Streams real-time forecast updates, evaluations, and alerts via Server-Sent Events (SSE)."""
    sub_id, queue = await stream_hub.register(target_filter=target)

    async def event_generator():
        try:
            # Initial connection confirmation
            init_data = json.dumps({
                "status": "connected",
                "subscriber_id": sub_id,
                "target": target or "*",
            })
            yield f"event: connected\ndata: {init_data}\n\n".encode()

            while True:
                # Check for client disconnect
                if await request.is_disconnected():
                    break

                try:
                    # Wait for next event with a 15-second heartbeat timeout
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                    sse_chunk = f"event: {msg['type']}\ndata: {json.dumps(msg, default=str)}\n\n"
                    yield sse_chunk.encode("utf-8")
                except TimeoutError:
                    # Send keep-alive heartbeat comment to prevent proxy idle drop
                    yield b": ping\n\n"
        finally:
            await stream_hub.unregister(sub_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/ws/forecasts")
async def websocket_forecasts(websocket: WebSocket) -> None:
    """Bidirectional WebSocket for interactive forecast streaming and pub/sub filtering."""
    await websocket.accept()
    sub_id, queue = await stream_hub.register(target_filter=None)

    # Task to pump events from queue to WebSocket
    async def _send_pump():
        try:
            while True:
                msg = await queue.get()
                await websocket.send_json(msg)
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass

    send_task = asyncio.create_task(_send_pump())

    try:
        # Send initial welcome
        await websocket.send_json({
            "type": "welcome",
            "subscriber_id": sub_id,
            "message": "Connected to Futuris Real-Time Forecast Stream",
            "timestamp": datetime.now(UTC).isoformat(),
        })

        while True:
            data = await websocket.receive_json()
            action = str(data.get("action", "")).lower()

            if action == "ping":
                await websocket.send_json({
                    "type": "pong",
                    "timestamp": datetime.now(UTC).isoformat(),
                })
            elif action == "subscribe":
                req_target = data.get("target")
                await stream_hub.update_filter(sub_id, req_target)
                await websocket.send_json({
                    "type": "subscribed",
                    "target": req_target or "*",
                    "status": "ok",
                })
            elif action == "unsubscribe":
                await stream_hub.update_filter(sub_id, None)
                await websocket.send_json({
                    "type": "unsubscribed",
                    "status": "ok",
                })
            else:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Unknown action '{action}'. Supported: ping, subscribe, ...",
                })
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        send_task.cancel()
        await stream_hub.unregister(sub_id)


@router.get("/streaming/status", summary="Real-time streaming hub metrics")
async def streaming_status() -> dict[str, Any]:
    """Inspect active SSE and WebSocket consumer status and broadcast metrics."""
    return {
        "status": "healthy",
        "service": "futuris:streaming",
        "hub": stream_hub.stats(),
        "observed_at": datetime.now(UTC).isoformat(),
    }
