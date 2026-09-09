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
    placeholders = {
        "changeme", "secret", "password"
    }
    if value.strip().lower() in placeholders:
        raise RuntimeError(f"placeholder secret rejected for {name}")


def production_env_guard(env: str, values: dict[str, str | None]) -> None:
    if env != "prod":
        return
    # Only enforce strict length/content in production when explicitly enabled
    if os.getenv("STRICT_PRODUCTION_SECRETS", "false").lower() != "true":
        return
    for name, value in values.items():
        if not value:
            raise RuntimeError(f"{name} must be configured in production")
        forbid_placeholder_secret(name, value)
        if len(value) < 10:
            raise RuntimeError(f"{name} is too short for production")
