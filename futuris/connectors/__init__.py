"""Input adapters, external data ingest connectors, and streaming feeds."""

from futuris.connectors.base import BaseConnector, Observation
from futuris.connectors.synthetic_telemetry import SyntheticTelemetryConnector

__all__ = ["BaseConnector", "Observation", "SyntheticTelemetryConnector"]
