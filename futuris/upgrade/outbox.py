from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Awaitable, Callable

from .persistence import DurableStateStore


@dataclass(frozen=True)
class OutboxEvent:
    event_id: str
    tenant_id: str
    event_type: str
    payload: dict


class OutboxDispatcher:
    def __init__(self, store: DurableStateStore) -> None:
        self.store = store
        self._lock = asyncio.Lock()

    async def publish(self, event: OutboxEvent) -> bool:
        return self.store.append_outbox(
            event.event_id, event.tenant_id, event.event_type, event.payload
        )

    async def drain(
        self, sender: Callable[[OutboxEvent], Awaitable[None]], *, limit: int = 100
    ) -> int:
        sent = 0
        async with self._lock:
            for row in self.store.pending_outbox(limit):
                event = OutboxEvent(
                    row["event_id"], row["tenant_id"], row["event_type"], json.loads(row["payload"])
                )
                try:
                    await sender(event)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    continue
                if self.store.mark_outbox_delivered(event.event_id):
                    sent += 1
        return sent
