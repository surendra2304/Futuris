"""Connector abstract base class and observation models."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class Observation(BaseModel):
    """Raw observation data point ingested from an external telemetry or signal source."""

    model_config = ConfigDict(extra="forbid")

    observation_id: UUID = Field(
        default_factory=uuid4,
        description="Unique identifier for the observation record.",
    )
    observed_at: datetime = Field(
        ...,
        description="Point-in-time UTC timestamp when the observation occurred.",
    )
    source: str = Field(
        ...,
        description="Identifier of the origin system or connector.",
    )
    series_id: str = Field(
        ...,
        description="Target series identifier (e.g. 'checkout:requests_per_minute').",
    )
    value: float = Field(
        ...,
        description="Numeric metric measurement.",
    )
    unit: str = Field(
        ...,
        description="Measurement unit (e.g. 'rpm', 'ms', 'percent').",
    )
    tags: dict[str, Any] = Field(
        default_factory=dict,
        description="Associated dimensional tags and metadata.",
    )


_warned_default_credentials: set[str] = set()


def warn_if_default_credential(
    connector_name: str,
    api_key: str,
    default_key: str,
    logger: Any,
) -> None:
    """Reject public built-in credentials in production and warn in development.

    These fallback strings are committed to source control, so they are not
    secrets. A remote peer accepting one would let unauthenticated deployments
    impersonate this connector. Development remains backward-compatible, but
    production fails closed before the connector can send an outbound request.
    """
    if api_key != default_key:
        return

    # Import here to keep the shared connector protocol independent of settings
    # during module initialization.
    from futuris.infra.config import settings
    from futuris.upgrade.safe_config import is_production_environment

    if is_production_environment(settings.APP_ENV):
        raise RuntimeError(
            f"{connector_name} connector refuses its built-in default credential in "
            "production; configure an explicit, unique secret before using this connector"
        )

    if connector_name not in _warned_default_credentials:
        _warned_default_credentials.add(connector_name)
        logger.warning(
            "connector_using_builtin_default_credential",
            connector=connector_name,
            remediation=(
                "configure a real API key for this connector; the built-in default "
                "is publicly known and must never be accepted by a peer"
            ),
        )


class BaseConnector(ABC):
    """Abstract Base Class for all signal, telemetry, and external data connectors."""

    @abstractmethod
    async def fetch(self, start: datetime, end: datetime) -> list[Observation]:
        """Fetch observations in the specified time window [start, end]."""
        raise NotImplementedError
