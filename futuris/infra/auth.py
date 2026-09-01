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
        return AuthUser(label="dev_admin", role="admin")

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
        return AuthUser(label="master_admin", role="admin")

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

    return AuthUser(label=record.label, role=record.role)


def require_role(min_role: str):
    """Dependency factory enforcing minimum required role hierarchy."""
    role_hierarchy = {"viewer": 1, "analyst": 2, "admin": 3}

    async def _role_checker(user: Annotated[AuthUser, Depends(get_current_user)]) -> AuthUser:
        user_weight = role_hierarchy.get(user.role, 0)
        req_weight = role_hierarchy.get(min_role, 0)

        if user_weight < req_weight:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions: requires minimum role '{min_role}'",
            )
        return user

    return _role_checker


RequireViewer = Annotated[AuthUser, Depends(require_role("viewer"))]
RequireAnalyst = Annotated[AuthUser, Depends(require_role("analyst"))]
RequireAdmin = Annotated[AuthUser, Depends(require_role("admin"))]
