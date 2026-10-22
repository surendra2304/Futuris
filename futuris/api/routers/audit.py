"""Audit log query router."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from futuris.api.deps import get_db_session
from futuris.infra.audit import AuditLogger, AuditRecord
from futuris.infra.auth import RequireAdmin

router = APIRouter(prefix="/v1/audit", tags=["Governance & Audit"])


@router.get("", response_model=list[AuditRecord], summary="List Audit Logs (Admin Only)")
async def list_audit_logs(
    _: RequireAdmin,
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
) -> list[AuditRecord]:
    """Retrieve immutable audit trail entries."""
    audit_logger = AuditLogger(session)
    return await audit_logger.list_recent(limit=limit)
