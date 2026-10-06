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


CPU_WORK_IN_FLIGHT = Gauge(
    "cpu_work_in_flight",
    "CPU-bound jobs currently running on the worker pool",
    ["kind"],
)

MODEL_SELECTION_DEGRADED_TOTAL = Counter(
    "model_selection_degraded_total",
    "Forecasts whose candidate backtest was cut short, by reason",
    ["reason"],
)

CPU_QUEUE_DEPTH = Gauge(
    "cpu_queue_depth",
    "Requests waiting for a CPU slot",
)

CPU_QUEUE_WAIT_SECONDS = Histogram(
    "cpu_queue_wait_seconds",
    "Time a request spent waiting for a CPU slot",
    ["kind"],
)


def metrics_endpoint() -> Response:
    """Return prometheus formatted metrics payload."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


PEER_CALL_TOTAL = Counter(
    "futuris_peer_calls_total",
    "Outbound calls to peer agents by outcome (ok, failed, short_circuited).",
    ["peer", "outcome"],
)

PEER_CIRCUIT_STATE = Gauge(
    "futuris_peer_circuit_state",
    "Circuit state per peer: 0 closed, 1 half-open, 2 open.",
    ["peer"],
)

SELF_HEALING_ACTIONS_TOTAL = Counter(
    "futuris_self_healing_actions_total",
    "Recovery actions taken by the self-healing supervisor.",
    ["action", "outcome"],
)

DEGRADED_SUBSYSTEMS = Gauge(
    "futuris_degraded_subsystems",
    "Number of subsystems the agent currently reports as degraded.",
)

