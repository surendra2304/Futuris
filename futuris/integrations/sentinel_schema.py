"""SENTINEL security event adapter translating security events into FUTURIS signals."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from futuris.connectors.base import Observation
from futuris.core.enums import SignalClass


class SentinelSecurityEvent(BaseModel):
    """Schema representing security incident logs emitted by SENTINEL."""

    event_id: UUID = Field(default_factory=uuid4)
    event_type: str = Field(..., description="E.g. authentication_brute_force, ddos_anomaly")
    asset_id: str = Field(..., description="Target service or infrastructure asset identifier")
    severity: str = Field(..., description="critical | high | medium | low")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    anomaly_count: int = Field(default=1, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


def translate_sentinel_event_to_observation(event: SentinelSecurityEvent) -> Observation:
    """Translate SENTINEL security event into a standardized FUTURIS telemetry Observation."""
    severity_weights = {
        "critical": 10.0,
        "high": 5.0,
        "medium": 2.0,
        "low": 1.0,
    }
    weight = severity_weights.get(event.severity.lower(), 1.0)
    composite_threat_value = float(event.anomaly_count) * weight

    return Observation(
        observed_at=event.timestamp,
        source=f"sentinel:{event.asset_id}",
        series_id=f"sentinel:{event.event_type}:{event.asset_id}",
        value=composite_threat_value,
        unit="threat_index",
        tags={
            "signal_class": SignalClass.AGENT_OBSERVATION.value,
            "severity": event.severity,
            "asset_id": event.asset_id,
            "raw_count": event.anomaly_count,
        },
    )


# Registered demonstration forecast target for security escalation
SENTINEL_DEMO_TARGET = "sentinel:incident_escalation_24h"
