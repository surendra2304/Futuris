"""Application configuration using Pydantic Settings."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for FUTURIS services and infrastructure."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_ENV: Literal["dev", "test", "prod"] = Field(
        default="dev",
        description="Application running environment mode.",
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
    FUTURIS_API_KEY: str = Field(
        default="futuris_api",
        description="Master API authentication key for Futuris",
    )
    API_KEYS_ENABLED: bool = Field(
        default=True,
        description="Whether API key authentication is enforced on API endpoints (always True).",
    )
    INFERENCE_URL: str = Field(
        default="https://inference-3i2b.onrender.com",
        description="Live Inference Gateway URL",
    )
    INFERENCE_API_KEY: str = Field(
        default="inference_api",
        description="Live Inference Gateway API Key",
    )
    MEMORA_URL: str = Field(
        default="https://memora-9zr9.onrender.com",
        description="Live Memora Cloud Memory URL",
    )
    MEMORA_API_KEY: str = Field(
        default="memora_api",
        description="Live Memora Cloud Memory API Key",
    )
    STRATEX_URL: str = Field(
        default="https://stratex-ucjz.onrender.com",
        description="Live Stratex Trading Bot URL",
    )
    STRATEX_API_KEY: str = Field(
        default="stratex_api",
        description="Live Stratex Trading Bot API Key",
    )
    INTELX_URL: str = Field(
        default="https://intelx-3cz1.onrender.com",
        description="Live IntelX Intelligence Engine URL",
    )
    INTELX_API_KEY: str = Field(
        default="intelx_api",
        description="Live IntelX Intelligence Engine API Key",
    )
    FUTURIS_FRIDAY_API_KEY: str = Field(
        default="friday_secret_key_default",
        description="FRIDAY ecosystem API Key",
    )

    def validate_production_safety(self) -> None:
        """Enforce safe_config checks if running under production environment."""
        from futuris.upgrade.safe_config import production_env_guard

        values = {
            "FUTURIS_API_KEY": self.FUTURIS_API_KEY,
            "INFERENCE_API_KEY": self.INFERENCE_API_KEY,
            "MEMORA_API_KEY": self.MEMORA_API_KEY,
            "STRATEX_API_KEY": self.STRATEX_API_KEY,
            "INTELX_API_KEY": self.INTELX_API_KEY,
            "FUTURIS_FRIDAY_API_KEY": self.FUTURIS_FRIDAY_API_KEY,
        }
        production_env_guard(self.APP_ENV, values)


settings = Settings()
if settings.APP_ENV == "prod":
    settings.validate_production_safety()
