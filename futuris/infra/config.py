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
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/futuris",
        description="Async connection string for PostgreSQL database.",
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
    API_KEYS_ENABLED: bool = Field(
        default=False,
        description="Whether API key authentication is enforced on API endpoints.",
    )


settings = Settings()
