"""Prometheus metrics registry and instrumentation."""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.responses import Response

FORECASTS_CREATED_TOTAL = Counter(
    "forecasts_created_total",
    "Total number of forecasts created",
    ["target", "model_version"],
)

FORECASTS_RESOLVED_TOTAL = Counter(
    "forecasts_resolved_total",
    "Total number of forecasts resolved",
    ["target", "resolution_method"],
)

FORECAST_LATENCY_SECONDS = Histogram(
    "forecast_latency_seconds",
    "Latency of forecasting pipeline stages in seconds",
    ["stage"],
)

CALIBRATION_ERROR_GAUGE = Gauge(
    "calibration_error_gauge",
    "Current expected calibration error (ECE)",
    ["target"],
)

WEBHOOK_DELIVERY_TOTAL = Counter(
    "webhook_delivery_total",
    "Total webhook deliveries with HTTP status code",
    ["status_code"],
)

SCENARIO_RUNS_TOTAL = Counter(
    "scenario_runs_total",
    "Total number of counterfactual scenarios evaluated",
    ["scenario_type"],
)

CONNECTOR_INGESTION_TOTAL = Counter(
    "connector_ingestion_total",
    "Total observations ingested by connector",
    ["connector", "status"],
)

MODEL_ACCURACY_GAUGE = Gauge(
    "model_accuracy_by_type",
    "Rolling empirical model accuracy by target type",
    ["target_type", "model_family"],
)


def metrics_endpoint() -> Response:
    """Return prometheus formatted metrics payload."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
