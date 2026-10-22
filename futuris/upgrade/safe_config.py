from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RequiredSecret:
    name: str
    minimum_length: int = 32


def required_secret(name: str, *, minimum_length: int = 32) -> str:
    value = os.getenv(name)
    if not value or len(value) < minimum_length:
        raise RuntimeError(f"required secret missing or too short: {name}")
    return value


def forbid_placeholder_secret(name: str, value: str) -> None:
    placeholders = PLACEHOLDER_SECRETS
    if value.strip().lower() in placeholders or "changeme" in value.strip().lower():
        raise RuntimeError(f"placeholder secret rejected for {name}")


PRODUCTION_ENVIRONMENTS = frozenset({"prod", "production"})

PLACEHOLDER_SECRETS = frozenset(
    {
        "changeme",
        "change_me",
        "secret",
        "password",
        "test",
        "demo",
        "dev",
        "development",
        "example",
        "placeholder",
        "your_api_key",
        "your-api-key",
        "todo",
        "none",
    }
)


def is_production_environment(env: str | None) -> bool:
    """True for every spelling of "production" this project has ever used."""
    return (env or "").strip().lower() in PRODUCTION_ENVIRONMENTS


def production_env_guard(
    env: str, values: dict[str, str | None], *, minimum_length: int = 32
) -> None:
    """Refuse to start in production with missing or placeholder credentials.

    This is unconditional: the previous implementation returned early unless
    ``STRICT_PRODUCTION_SECRETS=true`` was also set, which made the guard a
    no-op by default, and it only recognised the spelling ``prod``. A missing
    secret in production now always raises.
    """
    if not is_production_environment(env):
        return
    for name, value in values.items():
        if not value:
            raise RuntimeError(f"{name} must be configured in production")
        if len(value) < minimum_length:
            raise RuntimeError(
                f"{name} is too short for production (needs {minimum_length}+ characters)"
            )
        forbid_placeholder_secret(name, value)
