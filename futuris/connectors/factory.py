"""Connector factory: the telemetry source the unattended scheduler reads from.

The scheduler used to hardcode ``SyntheticTelemetryConnector``, so the
"autonomous" loop only ever forecast synthetic data no matter what was
configured. The source is now a configuration decision
(``FUTURIS_TELEMETRY_SOURCE``): the deterministic synthetic generator by
default, or the NEXUS telemetry broker when one is configured.
"""

from __future__ import annotations

from futuris.connectors.base import BaseConnector
from futuris.infra.config import settings


def build_scheduler_connector() -> BaseConnector:
    """Build the connector for the unattended scheduler from configuration."""
    if settings.FUTURIS_TELEMETRY_SOURCE == "nexus":
        from futuris.connectors.nexus import NexusConnector

        return NexusConnector(base_url=settings.NEXUS_URL, api_key=settings.NEXUS_API_KEY)
    from futuris.connectors.synthetic_telemetry import SyntheticTelemetryConnector

    return SyntheticTelemetryConnector(seed=42)
