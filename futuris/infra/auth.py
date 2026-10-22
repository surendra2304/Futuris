"""API Key authentication, role-based access control, and hashing."""

import hashlib
import hmac
import math
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from futuris.api.deps import get_db_session
from futuris.infra.config import settings
from futuris.storage.models import ApiKeyModel
from futuris.upgrade.rate_limit import InMemoryRateLimitBackend
from futuris.upgrade.safe_config import is_production_environment

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class AuthUser(BaseModel):
    """Authenticated identity and permissions."""

    label: str
    role: str  # anonymous | viewer | analyst | admin
    principal_id: str = "principal_default"
    tenant_id: str = "tenant_default"
    scopes: list[str] = []
    credential_id: str | None = None
    is_anonymous: bool = False


# Explicit anonymous principal. Read-only routes that are intentionally public
# (the dashboard) use it; every mutating route rejects it.
ANONYMOUS_VIEWER = AuthUser(
    label="anonymous",
    role="anonymous",
    principal_id="principal_anonymous",
    tenant_id="tenant_public",
    scopes=[],
    is_anonymous=True,
)

ROLE_HIERARCHY = {"anonymous": 0, "viewer": 1, "analyst": 2, "admin": 3}


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
    """FastAPI dependency resolving and verifying the API Key from header.

    Missing credentials resolve to the explicit anonymous principal (never a
    privileged one). Present-but-invalid credentials always fail with 401 --
    an invalid key is never silently downgraded to anonymous access.
    """
    # Key enforcement may be switched off for local development only. The
    # bypass is refused in production at request time as well as at config
    # validation, so no configuration mistake can turn production into an
    # all-admin service (B20).
    if not settings.API_KEYS_ENABLED and not is_production_environment(settings.APP_ENV):
        return AuthUser(
            label="dev_admin",
            role="admin",
            principal_id="principal_dev",
            tenant_id="tenant_dev",
            scopes=["*"],
        )

    if not raw_key:
        return ANONYMOUS_VIEWER

    # Clean Bearer prefix if passed via header
    clean_key = (
        raw_key.replace("Bearer ", "").strip() if raw_key.startswith("Bearer ") else raw_key.strip()
    )

    # Master API key check (e.g. FUTURIS_API_KEY from environment).
    # Constant-time comparison, same as every other credential check in the
    # codebase: a plain == is a (theoretical) timing side channel.
    if settings.FUTURIS_API_KEY and hmac.compare_digest(clean_key, settings.FUTURIS_API_KEY):
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


def _require_role(user: AuthUser, minimum: int, role_name: str) -> AuthUser:
    if ROLE_HIERARCHY.get(user.role, 0) < minimum:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Insufficient permissions: requires minimum role '{role_name}'",
        )
    return user


async def require_viewer(user: Annotated[AuthUser, Depends(get_current_user)]) -> AuthUser:
    """Require an authenticated credential with at least viewer privileges."""
    return _require_role(user, 1, "viewer")


async def require_analyst(user: Annotated[AuthUser, Depends(get_current_user)]) -> AuthUser:
    """Require an authenticated credential with at least analyst privileges."""
    return _require_role(user, 2, "analyst")


async def require_admin(user: Annotated[AuthUser, Depends(get_current_user)]) -> AuthUser:
    """Require an authenticated credential with admin privileges."""
    return _require_role(user, 3, "admin")


# Anonymous callers share no credential, so the only handle on them is the
# client address. The budget is per process (like the FRIDAY limiter): a shared
# limiter must replace it before running more than one replica (S07).
anonymous_read_limiter = InMemoryRateLimitBackend()
ANONYMOUS_WINDOW_SECONDS = 60.0


def client_address(request: Request) -> str:
    """Address used as the anonymous-budget key.

    ``X-Forwarded-For`` is honoured only when ``TRUST_PROXY_HEADERS`` is set. The
    rightmost entry is the one the nearest proxy appended; the leftmost entries
    are whatever the client sent and can be forged.
    """
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for", "")
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if hops:
            return hops[-1]
    return request.client.host if request.client else "unknown"


async def _charge_anonymous_budget(request: Request, user: AuthUser, *, bucket: str, limit: int):
    """Spend one unit of the anonymous budget. Authenticated callers are not charged here."""
    if not user.is_anonymous:
        return
    key = f"anonymous:{bucket}:{client_address(request)}"
    decision = await anonymous_read_limiter.consume(
        key, limit=limit, window_seconds=ANONYMOUS_WINDOW_SECONDS
    )
    if not decision.allowed:
        retry_after = max(1, math.ceil(decision.retry_after_seconds))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Anonymous request budget exhausted ({limit} per minute per client). "
                f"Retry after {retry_after}s, or authenticate with an API key."
            ),
            headers={"Retry-After": str(retry_after)},
        )


async def allow_anonymous_read(
    request: Request,
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> AuthUser:
    """Permit the public read-only dashboard while still resolving real credentials.

    Used only by read routes whose payload is intentionally public (forecast
    records, registry metadata, calibration curves, peer probes). Anonymous
    callers draw on a per-client budget; a 429 with ``Retry-After`` is returned
    when it is spent.
    """
    await _charge_anonymous_budget(
        request, user, bucket="read", limit=settings.ANONYMOUS_READ_RATE_LIMIT_PER_MINUTE
    )
    return user


async def allow_anonymous_heavy_read(
    request: Request,
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> AuthUser:
    """Anonymous read that computes or persists: a much smaller per-client budget."""
    await _charge_anonymous_budget(
        request,
        user,
        bucket="heavy",
        limit=settings.ANONYMOUS_HEAVY_READ_RATE_LIMIT_PER_MINUTE,
    )
    return user


RequireViewer = Annotated[AuthUser, Depends(require_viewer)]
RequireAnalyst = Annotated[AuthUser, Depends(require_analyst)]
RequireAdmin = Annotated[AuthUser, Depends(require_admin)]
AllowAnonymousRead = Annotated[AuthUser, Depends(allow_anonymous_read)]
AllowAnonymousHeavyRead = Annotated[AuthUser, Depends(allow_anonymous_heavy_read)]
