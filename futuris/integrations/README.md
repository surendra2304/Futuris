# FUTURIS Ecosystem Integration Surface & Boundaries

FUTURIS is designed as a standalone predictive-intelligence and forecasting kernel. This document defines the integration contracts and architectural safety boundaries for upstream data providers (NEXUS), security telemetry (SENTINEL), and downstream autonomous consumers (FRIDAY).

---

## 🏛️ The Fundamental Architectural Boundary

> **Prediction $\neq$ Authorization $\neq$ Execution**
>
> 1. **FUTURIS** predicts future states, computes calibrated probabilities, measures uncertainty ranges, and evaluates counterfactual scenarios.
> 2. **FRIDAY** plans, coordinates, orchestrates, and authorizes actions.
> 3. **Execution tools and actuators** live in their respective execution planes.
> 4. FUTURIS **never** executes automated scaling, shedding, or mitigation actions directly.

---

## 📥 Ingestion Contracts (What FUTURIS Consumes)

FUTURIS consumes point-in-time time-series telemetry strictly via the `BaseConnector` interface:

- **Schema**: `Observation(observed_at: datetime, source: str, series_id: str, value: float, unit: str, tags: dict)`
- **NEXUS Connector** (`NexusConnector`): Ingests observations over authenticated HTTP (`Bearer token`).
- **SENTINEL Security Adapter** (`SentinelSecurityEvent`): Translates structured security incidents into normalized threat-index signals (`SignalClass.AGENT_OBSERVATION`).

---

## 📤 Emission Contracts (What FUTURIS Emits)

1. **REST API (`/v1`)**:
   - `POST /v1/forecasts`: On-demand predictions with point-in-time evidence snapshots.
   - `POST /v1/forecasts/{id}/scenarios/compare`: Side-by-side scenario divergence matrices.
   - `GET /v1/evaluation/calibration`: Honesty reliability curves and Expected Calibration Error ($ECE$).

2. **Domain Events & Webhooks**:
   - Webhook payloads are signed with `HMAC-SHA256` in the `X-Futuris-Signature` header.
   - Events emitted: `forecast_created`, `forecast_updated`, `forecast_threshold_crossed`, `forecast_invalidated`, `forecast_outcome_recorded`, `model_promoted`, `model_degraded`.

---

## 💻 Consumer SDK (`FridayClient`)

Downstream platforms consume FUTURIS via the typed Python SDK:
```python
from futuris.integrations.friday_client import FridayClient

client = FridayClient(base_url="http://127.0.0.1:8000")
forecast = await client.request_forecast(
    target="service:checkout:capacity_exceedance_24h",
    horizon="24h",
)
```