"""API Key authentication, role-based access control, and hashing."""

import hashlib
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from futuris.api.deps import get_db_session
from futuris.infra.config import settings
from futuris.storage.models import ApiKeyModel

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class AuthUser(BaseModel):
    """Authenticated identity and permissions."""

    label: str
    role: str  # viewer | analyst | admin
    principal_id: str = "principal_default"
    tenant_id: str = "tenant_default"
    scopes: list[str] = []
    credential_id: str | None = None


def hash_api_key(plain_key: str) -> str:
    """Compute SHA-256 digest of plain API key."""
    return hashlib.sha256(plain_key.encode("utf-8")).hexdigest()


def generate_api_key(prefix: str = "futuris") -> tuple[str, str]:
    """Generate a high-entropy API key and return (plain_key, key_hash)."""
    raw_token = secrets.token_hex(24)
    plain_key = f"{prefix}_{raw_token}"
    return plain_key, hash_api_key(plain_key)


async def get_current_user(
    raw_key: str | None = Security(api_key_header),
    session: AsyncSession = Depends(get_db_session),
) -> AuthUser:
    """FastAPI dependency resolving and verifying the API Key from header."""
    # Check if auth enforcement is disabled in development
    if not getattr(settings, "API_KEYS_ENABLED", True) or getattr(settings, "AUTH_DISABLED", False):
        return AuthUser(
            label="dev_admin",
            role="admin",
            principal_id="principal_dev",
            tenant_id="tenant_dev",
            scopes=["*"],
        )

    if not raw_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key authentication header",
        )

    # Clean Bearer prefix if passed via header
    clean_key = (
        raw_key.replace("Bearer ", "").strip()
        if raw_key.startswith("Bearer ")
        else raw_key.strip()
    )

    # Master API key check (e.g. FUTURIS_API_KEY from environment)
    if clean_key == settings.FUTURIS_API_KEY:
        return AuthUser(
            label="master_admin",
            role="admin",
            principal_id="principal_master",
            tenant_id="tenant_master",
            scopes=["*"],
        )

    key_hash = hash_api_key(clean_key)
    stmt = select(ApiKeyModel).where(
        ApiKeyModel.key_hash == key_hash,
        ApiKeyModel.revoked_at.is_(None),
    )
    res = await session.execute(stmt)
    record = res.scalar_one_or_none()

    if not record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API Key",
        )

    return AuthUser(
        label=record.label,
        role=record.role,
        principal_id=f"principal_{record.label}",
        tenant_id=getattr(record, "tenant_id", "tenant_default"),
        scopes=[record.role],
        credential_id=record.key_hash[:16],
    )


async def require_viewer(user: Annotated[AuthUser, Depends(get_current_user)]) -> AuthUser:
    role_hierarchy = {"viewer": 1, "analyst": 2, "admin": 3}
    if role_hierarchy.get(user.role, 0) < 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions: requires minimum role 'viewer'",
        )
    return user


async def require_analyst(user: Annotated[AuthUser, Depends(get_current_user)]) -> AuthUser:
    role_hierarchy = {"viewer": 1, "analyst": 2, "admin": 3}
    if role_hierarchy.get(user.role, 0) < 2:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions: requires minimum role 'analyst'",
        )
    return user


async def require_admin(user: Annotated[AuthUser, Depends(get_current_user)]) -> AuthUser:
    role_hierarchy = {"viewer": 1, "analyst": 2, "admin": 3}
    if role_hierarchy.get(user.role, 0) < 3:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions: requires minimum role 'admin'",
        )
    return user


RequireViewer = Annotated[AuthUser, Depends(require_viewer)]
RequireAnalyst = Annotated[AuthUser, Depends(require_analyst)]
RequireAdmin = Annotated[AuthUser, Depends(require_admin)]
