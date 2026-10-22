from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class StoredCredential:
    credential_id: str
    principal_id: str
    tenant_id: str
    salt_b64: str
    verifier_b64: str
    iterations: int
    created_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


class CredentialHasher:
    """Password-style API-key verifier; plaintext secrets are never persisted."""

    def __init__(self, iterations: int = 310_000) -> None:
        if iterations < 100_000:
            raise ValueError("PBKDF2 iteration count is too low")
        self.iterations = iterations

    def hash_secret(self, secret: str, *, salt: bytes | None = None) -> tuple[str, str]:
        if len(secret) < 20:
            raise ValueError("secret must be at least 20 characters")
        salt_bytes = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", secret.encode(), salt_bytes, self.iterations, dklen=32
        )
        return (
            base64.urlsafe_b64encode(salt_bytes).decode(),
            base64.urlsafe_b64encode(digest).decode(),
        )

    def verify(self, secret: str, stored: StoredCredential) -> bool:
        try:
            salt = base64.urlsafe_b64decode(stored.salt_b64.encode())
            expected = base64.urlsafe_b64decode(stored.verifier_b64.encode())
        except Exception:
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", secret.encode(), salt, stored.iterations, dklen=len(expected)
        )
        return hmac.compare_digest(candidate, expected)


def validate_production_credentials(
    *,
    environment: str,
    master_key: str | None,
    service_keys: dict[str, str | None],
    allow_demo_credentials: bool,
) -> None:
    if environment not in {"dev", "test", "prod"}:
        raise ValueError("unknown application environment")
    if environment == "prod" and allow_demo_credentials:
        raise ValueError("demo credentials cannot be enabled in production")
    if environment == "prod" and (not master_key or len(master_key) < 32):
        raise ValueError("production master credential must be explicitly configured")
    if environment == "prod":
        missing = [name for name, key in service_keys.items() if not key or len(key) < 32]
        if missing:
            raise ValueError(f"missing production service credentials: {','.join(missing)}")


def parse_api_key_header(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    return value or None


def credential_expired(stored: StoredCredential, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return stored.revoked_at is not None or (
        stored.expires_at is not None and stored.expires_at <= now
    )


def new_expiry(days: int) -> datetime:
    if days <= 0:
        raise ValueError("expiry must be positive")
    return datetime.now(UTC) + timedelta(days=days)
