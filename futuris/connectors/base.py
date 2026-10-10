"""Connector abstract base class and observation models."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Observation(BaseModel):
    """Raw observation data point ingested from an external telemetry or signal source."""

    model_config = ConfigDict(extra="forbid")

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
    """Log a loud, once-per-process warning when a built-in default key is in use.

    Several connectors historically fell back to a hardcoded default credential
    (``nexus_default_token``, ``forge_default_secret_key``, ...). Those strings
    are public -- they live in this repository -- so a peer that accepts them
    is a backdoor, and a deployment that forgot to configure a key was silently
    authenticating with a guessable value. The warning makes the hazard visible
    in the logs at the moment the connector is constructed.
    """
    if api_key == default_key and connector_name not in _warned_default_credentials:
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
