"""Application configuration using Pydantic Settings."""

from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for FUTURIS services and infrastructure."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_ENV: str = Field(
        default="dev",
        description=(
            "Application running environment mode. Defaults to dev so a fresh "
            "checkout never claims production; production must be set explicitly "
            "and then has to carry real credentials (see production_env_guard)."
        ),
    )
    SELF_HEALING_ENABLED: bool = Field(
        default=True,
        description="Run the periodic self-assessment and self-healing supervisor.",
    )
    SELF_HEALING_INTERVAL_SECONDS: float = Field(
        default=60.0,
        description="Seconds between self-assessment passes.",
    )
    SCHEDULER_ENABLED: bool = Field(
        default=True,
        description="Run the unattended ingestion/refresh/lifecycle scheduler in-process.",
    )
    ALLOW_DEMO_CREDENTIALS: bool = Field(
        default=False,
        description="Permit demo credentials (never allowed in production)",
    )
    STARTUP_DEMO_SEED_ENABLED: bool = Field(
        default=False,
        description=(
            "Allow synthetic demo forecasts to seed an empty non-production database at startup. "
            "Production startup never seeds synthetic demo telemetry."
        ),
    )
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./data/futuris.db",
        description="Async connection string for database (SQLite or PostgreSQL).",
    )
    OBJECT_STORE_PATH: str = Field(
        default="./data/storage",
        description="Local or mounted directory path for evidence and snapshot storage.",
    )
    LLM_PROVIDER: Literal["anthropic", "openai", "none"] = Field(
        default="none",
        description="Supported LLM vendor provider for agent reasoning.",
    )
    LLM_API_KEY: str | None = Field(
        default=None,
        description="API key for chosen LLM provider if enabled.",
    )
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Logging level threshold.",
    )
    FUTURIS_API_KEY: str | None = Field(
        default=None,
        description="Master API authentication key for Futuris",
    )
    API_KEYS_ENABLED: bool = Field(
        default=True,
        description="Whether API key authentication is enforced on API endpoints (always True).",
    )
    INFERENCE_URL: str = Field(
        default="https://inference-h7bn.onrender.com",
        description="Live Inference Gateway URL",
    )
    INFERENCE_API_KEY: str | None = Field(
        default=None,
        description="Live Inference Gateway API Key",
    )
    MEMORA_URL: str = Field(
        default="https://memora-cavc.onrender.com",
        description="Live Memora Cloud Memory URL",
    )
    MEMORA_API_KEY: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "FUTURIS_MEMORA_API_KEY", "FUTURIS_API_KEY", "MEMORA_API_KEY"
        ),
        description="Live Memora Cloud Memory API Key",
    )
    STRATEX_URL: str = Field(
        default="https://stratex-8wj1.onrender.com",
        description="Live Stratex Trading Bot URL",
    )
    STRATEX_API_KEY: str | None = Field(
        default=None,
        description="Live Stratex Trading Bot API Key",
    )
    INTELX_URL: str = Field(
        default="https://intelx-mygl.onrender.com",
        description="Live IntelX Intelligence Engine URL",
    )
    INTELX_API_KEY: str | None = Field(
        default=None,
        description="Live IntelX Intelligence Engine API Key",
    )
    CORTEX_URL: str = Field(
        default="https://cortex-0m7c.onrender.com",
        description="Live Cortex URL",
    )
    FORGE_URL: str = Field(
        default="https://forge-e9kl.onrender.com",
        description="Live Forge URL",
    )
    SENTINEL_URL: str = Field(
        default="https://sentinel-a861.onrender.com",
        description="Live Sentinel URL",
    )
    FRIDAY_URL: str = Field(
        default="https://friday-zw59.onrender.com",
        description="Live Friday URL",
    )
    FUTURIS_TELEMETRY_SOURCE: Literal["synthetic", "nexus"] = Field(
        default="synthetic",
        description=(
            "Telemetry source for the unattended scheduler: the deterministic "
            "synthetic generator (default) or the NEXUS telemetry broker."
        ),
    )
    NEXUS_URL: str = Field(
        default="http://nexus-service.local",
        description="NEXUS telemetry broker URL (used when FUTURIS_TELEMETRY_SOURCE=nexus)",
    )
    NEXUS_API_KEY: str | None = Field(
        default=None,
        description="NEXUS telemetry broker API key",
    )
    FUTURIS_FRIDAY_API_KEY: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FUTURIS_FRIDAY_API_KEY", "FRIDAY_API_KEY"),
        description="FRIDAY ecosystem API Key (FRIDAY_API_KEY accepted as an alias)",
    )
    INTELX_WEBHOOK_API_KEY: str | None = Field(
        default=None,
        validation_alias=AliasChoices("INTELX_WEBHOOK_API_KEY"),
        description="Shared secret for inbound IntelX research webhooks",
    )

    DOCS_ENABLED: bool | None = Field(
        default=None,
        description=(
            "Serve /docs, /redoc and /openapi.json. Unset (null) means enabled outside "
            "production and disabled in production; set true or false to override."
        ),
    )
    ANONYMOUS_READ_RATE_LIMIT_PER_MINUTE: int = Field(
        default=120,
        ge=1,
        le=100000,
        description="Requests per minute per client address for anonymous reads (no API key).",
    )
    ANONYMOUS_HEAVY_READ_RATE_LIMIT_PER_MINUTE: int = Field(
        default=6,
        ge=1,
        le=100000,
        description=(
            "Requests per minute per client address for anonymous reads that compute or "
            "persist forecasts: GET /v1/futuris/forecast, GET /v1/market/forecast, "
            "GET /v1/predictions/matrix."
        ),
    )
    TRUST_PROXY_HEADERS: bool = Field(
        default=False,
        description=(
            "Take the client address from the rightmost X-Forwarded-For entry. Enable only "
            "behind a reverse proxy that appends to X-Forwarded-For; otherwise clients can "
            "spoof their address and escape the anonymous budget."
        ),
    )

    def validate_production_safety(self) -> None:
        """Enforce safe_config checks if running under production environment."""
        from futuris.upgrade.safe_config import (
            is_production_environment,
            production_env_guard,
        )

        # Disabling key enforcement grants every request full admin. That is a
        # development convenience and must never reach production, whatever
        # the other credentials look like (B20).
        if is_production_environment(self.APP_ENV) and not self.API_KEYS_ENABLED:
            raise RuntimeError(
                "API_KEYS_ENABLED=false is refused in production: it would grant every "
                "request full admin access"
            )

        values = {
            "FUTURIS_API_KEY": self.FUTURIS_API_KEY,
            "INFERENCE_API_KEY": self.INFERENCE_API_KEY,
            "MEMORA_API_KEY": self.MEMORA_API_KEY,
            "STRATEX_API_KEY": self.STRATEX_API_KEY,
            "INTELX_API_KEY": self.INTELX_API_KEY,
            "FUTURIS_FRIDAY_API_KEY": self.FUTURIS_FRIDAY_API_KEY,
        }
        production_env_guard(self.APP_ENV, values)

    def validate_credential_contract(self) -> None:
        """Validate the master/service credential contract for production."""
        from futuris.upgrade.auth import validate_production_credentials

        validate_production_credentials(
            environment="prod",
            master_key=self.FUTURIS_API_KEY,
            service_keys={
                "FUTURIS_FRIDAY_API_KEY": self.FUTURIS_FRIDAY_API_KEY,
                "MEMORA_API_KEY": self.MEMORA_API_KEY,
                "INTELX_API_KEY": self.INTELX_API_KEY,
            },
            allow_demo_credentials=self.ALLOW_DEMO_CREDENTIALS,
        )


settings = Settings()
# Production safety is enforced unconditionally at import: an environment that
# claims to be production but carries placeholder credentials must not start.
from futuris.upgrade.safe_config import is_production_environment  # noqa: E402

if is_production_environment(settings.APP_ENV):
    settings.validate_production_safety()
    settings.validate_credential_contract()
