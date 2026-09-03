from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from .models import AuditEvent


SENSITIVE_KEYS = {
    "authorization", "api_key", "apikey", "token", "secret",
    "password", "cookie", "credential", "inference_api_key",
}


def redact_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if k.lower() in SENSITIVE_KEYS else redact_mapping(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_mapping(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_mapping(v) for v in value)
    return value


class AuditLog:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = asyncio.Lock()

    async def append(self, event: AuditEvent) -> AuditEvent:
        safe = AuditEvent(
            event_id=event.event_id,
            tenant_id=event.tenant_id,
            principal_id=event.principal_id,
            action=event.action,
            resource=event.resource,
            outcome=event.outcome,
            reason=event.reason,
            request_id=event.request_id,
            at=event.at,
            metadata=redact_mapping(event.metadata),
        )
        async with self._lock:
            self._events.append(safe)
        return safe

    async def query(self, *, tenant_id: str, limit: int = 100) -> list[AuditEvent]:
        if limit < 1 or limit > 1000:
            raise ValueError("invalid limit")
        async with self._lock:
            rows = [e for e in self._events if e.tenant_id == tenant_id]
            return list(reversed(rows[-limit:]))

    async def record(
        self, tenant_id: str, principal_id: str, action: str, resource: str, outcome: str, reason: str = "", **metadata: Any
    ) -> AuditEvent:
        return await self.append(
            AuditEvent(
                event_id=uuid4(),
                tenant_id=tenant_id,
                principal_id=principal_id,
                action=action,
                resource=resource,
                outcome=outcome,
                reason=reason,
                metadata=metadata,
            )
        )
