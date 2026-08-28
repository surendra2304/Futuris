"""Append-only audit trail logger for mutating actions."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from futuris.storage.models import AuditLogModel


class AuditRecord(BaseModel):
    """Structured audit log entry."""

    audit_id: UUID
    actor_label: str
    action: str
    entity: str
    entity_id: str
    payload_hash: str
    timestamp: datetime


class AuditLogger:
    """Append-only audit log manager recording all state mutations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def compute_payload_hash(payload: Any) -> str:
        """Compute SHA-256 digest of serialized payload."""
        if isinstance(payload, str):
            raw = payload.encode("utf-8")
        elif isinstance(payload, bytes):
            raw = payload
        else:
            try:
                raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
            except Exception:
                raw = str(payload).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    async def log_mutation(
        self,
        actor_label: str,
        action: str,
        entity: str,
        entity_id: str,
        payload: Any,
    ) -> AuditRecord:
        """Append an immutable audit entry."""
        audit_id = uuid4()
        now = datetime.now(UTC)
        p_hash = self.compute_payload_hash(payload)

        model = AuditLogModel(
            audit_id=audit_id,
            actor_label=actor_label,
            action=action,
            entity=entity,
            entity_id=entity_id,
            payload_hash=p_hash,
            timestamp=now,
        )
        self.session.add(model)
        await self.session.flush()

        return AuditRecord(
            audit_id=audit_id,
            actor_label=actor_label,
            action=action,
            entity=entity,
            entity_id=entity_id,
            payload_hash=p_hash,
            timestamp=now,
        )

    async def list_recent(self, limit: int = 100) -> list[AuditRecord]:
        """Query audit log entries in reverse chronological order."""
        stmt = select(AuditLogModel).order_by(AuditLogModel.timestamp.desc()).limit(limit)
        res = await self.session.execute(stmt)
        return [
            AuditRecord(
                audit_id=m.audit_id,
                actor_label=m.actor_label,
                action=m.action,
                entity=m.entity,
                entity_id=m.entity_id,
                payload_hash=m.payload_hash,
                timestamp=m.timestamp,
            )
            for m in res.scalars().all()
        ]
