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


class BaseConnector(ABC):
    """Abstract Base Class for all signal, telemetry, and external data connectors."""

    @abstractmethod
    async def fetch(self, start: datetime, end: datetime) -> list[Observation]:
        """Fetch observations in the specified time window [start, end]."""
        raise NotImplementedError
