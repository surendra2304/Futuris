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

LLM_CALLS_TOTAL = Counter(
    "llm_calls_total",
    "Total LLM agent calls and rule-based fallback counter",
    ["provider", "status"],
)


def metrics_endpoint() -> Response:
    """Return prometheus formatted metrics payload."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
