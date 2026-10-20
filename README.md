# FUTURIS — Temporal Modeling, Scenario Simulation & Predictive Intelligence Specialist

FUTURIS is the dedicated temporal modeling, probabilistic forecasting, counterfactual scenario simulation, and predictive intelligence specialist of the **FRIDAY Universe**.

Built on statistical calibration, conformal prediction, and immutable evidence anchoring, FUTURIS forecasts operational capacity boundaries, market regimes, and risk vectors across our private multi-container mesh deployed on Oracle Cloud Always Free.

> **Architectural Invariant — Prediction $\neq$ Authorization:**
> FUTURIS generates calibrated predictive distributions, uncertainty bounds, and advisory decision boundaries. It strictly **never** executes system commands, triggers destructive mitigations, or modifies infrastructure without explicit external governance authorization.

---

## 🌐 Topology & Mesh Architecture

FUTURIS is deployed on an Oracle Cloud Always Free host (Ampere A1 ARM64 / AMD x86_64) running within the unified private container mesh (`friday_mesh`) with automated HTTPS termination via Caddy.

| Parameter | Value | Description |
| :--- | :--- | :--- |
| **Identity** | `futuris` | Temporal modeling & predictive intelligence engine |
| **Internal Container Port** | `8000` | Port bound inside the container |
| **Host Port Mapping** | `8004:8000` | Host port on Oracle Cloud instance |
| **Docker Mesh URL** | `http://futuris:8000` | Private container mesh address within `friday_mesh` |
| **Public Gateway Route** | `https://friday-universe.duckdns.org/futuris/*` | Reverse-proxy routed via Caddy |
| **FRIDAY Core Gateway** | `http://friday:10000` | Primary ecosystem orchestrator |
| **Database Engines** | PostgreSQL (`futuris_db`) / SQLite 3 WAL | `dokploy-postgres` & high-concurrency local WAL |
| **Target Runtime** | Python 3.11+ / FastAPI | Non-root container (`appuser`, UID 1000) |

```
                              [ Internet / Public Clients ]
                                            │
                                            ▼ HTTPS (443)
                         ┌─────────────────────────────────────┐
                         │   Caddy Reverse Proxy (HTTPS/SSL)   │
                         │   https://friday-universe.duckdns.org│
                         └──────────────────┬──────────────────┘
                                            │
               /futuris/*                   │                   /
          ┌─────────────────────────────────┴────────────────────────┐
          ▼                                                          ▼
┌───────────────────────────────┐                          ┌──────────────────┐
│   FUTURIS Specialist (8000)   │ ◄──── friday_mesh ─────► │   FRIDAY Core    │
│   Host Port: 8004             │                          │   Port: 10000    │
└──────────────┬────────────────┘                          └──────────────────┘
               │
               ▼
┌───────────────────────────────┐
│   SQLite 3 WAL (futuris.db)   │
│   PRAGMA journal_mode=WAL     │
│   PRAGMA busy_timeout=30000   │
└───────────────────────────────┘
```

---

## 💾 Production Database Storage (PostgreSQL & SQLite WAL)

FUTURIS supports production-grade dual persistence:
1. **Production PostgreSQL (`dokploy-postgres`)**:
   - Host: `dokploy-postgres:5432`, Database: `futuris_db`, User: `futuris_user`
   - Connection: `asyncpg` async driver with pool recycling, keepalives, and pre-ping checks.
   - 12 Provisioned Tables: `forecasts`, `evidence_refs`, `observations`, `outcomes`, `scenarios`, `forecast_events`, `model_registry`, `evaluation_runs`, `intelx_notices`, `signal_sources`, `api_keys`, `audit_logs`.
2. **Local High-Concurrency SQLite 3 WAL Mode**:
   - Storage Location: `data/futuris.db`
   - WAL Mode: `PRAGMA journal_mode=WAL;` and `PRAGMA busy_timeout=30000;` to absorb concurrent bursts.
   - Graceful Shutdown: `SIGTERM`/`SIGINT` trapping with `PRAGMA wal_checkpoint(TRUNCATE);`.

### 🛡️ Core Safety Invariants
- **Safety Invariant 1 (Strict Advisory Invariant)**: Forecasts are probabilistic advisory insights only (`prediction_is_not_authorization: true`). They strictly cannot execute trades, mutate servers, or release software autonomously (`executable_commands: []`).
- **Safety Invariant 2 (Input Provenance Gate)**: Predictions require verified live input telemetry. The UI strictly withholds numerical forecast values (`WITHHELD [Unverified Input Telemetry]`) whenever evidence class is synthetic or unverified, and surfaces a dedicated `VERIFIED LIVE INPUT` badge only for live telemetry.
- **Safety Invariant 3 (Brier Score Penalization)**: Historical calibration penalizes overconfidence. Calibration sweeps compute Brier scores and ECE across resolved outcomes, automatically widening uncertainty intervals `[range_lower, range_upper]` when overconfidence is detected.

---

## 🔌 API Specification & Core Endpoints

### 1. Health Probe (`/health`)
Proves process liveness and reports a measured SQLite storage schema verification verdict.

- **Method**: `GET` / `HEAD`
- **Path**: `/health` (also accessible via `https://friday-universe.duckdns.org/futuris/health`)

**cURL Example**:
```bash
curl -s http://localhost:8000/health
```

**Response Payload (HTTP 200 OK)**:
```json
{
  "status": "ok",
  "service": "futuris",
  "evidence_class": "process_liveness",
  "observed_at": "2026-10-14T21:40:00.123456+00:00",
  "version": "1.0.0",
  "storage": {
    "ready": true,
    "expected": 12,
    "present": 12,
    "missing": [],
    "error": null
  }
}
```

---

### 2. Readiness Probe (`/ready`)
Validates active database round-trip execution (`SELECT 1;`) and table schema completeness before admitting traffic from the gateway.

- **Method**: `GET` / `HEAD`
- **Path**: `/ready`

**cURL Example**:
```bash
curl -s http://localhost:8000/ready
```

**Response Payload (HTTP 200 OK)**:
```json
{
  "status": "ready",
  "service": "futuris",
  "database": "connected",
  "version": "1.0.0",
  "observed_at": "2026-10-14T21:40:00.123456+00:00"
}
```

---

### 3. Service Status & Diagnostics (`/status`)
Exposes detailed operational telemetry, memory footprint, cache performance, and peer circuit breaker states.

- **Method**: `GET`
- **Path**: `/status`

**cURL Example**:
```bash
curl -s http://localhost:8000/status
```

**Response Payload (HTTP 200 OK)**:
```json
{
  "status": "healthy",
  "service": "futuris",
  "role": "Temporal Modeling, Scenario Simulation & Predictive Intelligence",
  "version": "1.0.0",
  "environment": "prod",
  "port": 8000,
  "uptime_seconds": 3612.45,
  "observed_at": "2026-10-14T21:40:00.123456+00:00",
  "memory": {
    "rss_mb": 78.42,
    "vms_mb": 184.20
  },
  "cache": {
    "size": 14,
    "max_size": 2000,
    "hits": 342,
    "misses": 28,
    "hit_ratio": 0.9243,
    "evictions": 0,
    "expirations": 4
  },
  "circuit_breakers": [
    {
      "peer": "inference",
      "state": "closed",
      "consecutive_failures": 0,
      "total_calls": 45,
      "total_failures": 0,
      "retry_in_seconds": 0.0
    }
  ],
  "scheduler_active": true
}
```

---

### 4. Probabilistic Forecast (`/forecast`)
Generates calibrated probabilistic forecasts and uncertainty bands for any target metric over a specified horizon.

- **Method**: `POST`
- **Path**: `/forecast` (or `/v1/forecasts`)

**Request Payload**:
```json
{
  "target": "service:checkout:capacity_exceedance_24h",
  "horizon": "24h"
}
```

**cURL Example**:
```bash
curl -X POST http://localhost:8000/forecast \
  -H "Content-Type: application/json" \
  -d '{"target": "service:checkout:capacity_exceedance_24h", "horizon": "24h"}'
```

**Response Payload (HTTP 200 OK)**:
```json
{
  "forecast_id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
  "target": "service:checkout:capacity_exceedance_24h",
  "prediction": 842.50,
  "range": {
    "lower": 810.20,
    "central": 842.50,
    "upper": 874.80
  },
  "probability": 0.88,
  "confidence": "high",
  "model": "AutoARIMA-v1.4",
  "as_of": "2026-10-14T21:40:00.123456+00:00",
  "horizon": "24h",
  "status": "active",
  "prediction_is_not_authorization": true,
  "executable_commands": []
}
```

---

### 5. Counterfactual Scenario Simulation (`/simulate`)
Simulates what-if scenarios and stress conditions on top of a parent forecast baseline without altering historical or base telemetry.

- **Method**: `POST`
- **Path**: `/simulate` (or `/v1/forecasts/{forecast_id}/scenarios`)

**Request Payload**:
```json
{
  "base_forecast_id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
  "name": "Black Friday Traffic Surge",
  "is_stress": true,
  "assumption_overrides": {
    "demand_multiplier": 1.6,
    "capacity_multiplier": 0.75
  },
  "rationale": "Evaluate headroom if checkout cluster suffers node drop during marketing surge"
}
```

**cURL Example**:
```bash
curl -X POST http://localhost:8000/simulate \
  -H "Content-Type: application/json" \
  -d '{
    "base_forecast_id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
    "name": "Black Friday Traffic Surge",
    "is_stress": true,
    "assumption_overrides": {"demand_multiplier": 1.6, "capacity_multiplier": 0.75}
  }'
```

**Response Payload (HTTP 200 OK)**:
```json
{
  "scenario_id": "e7f8a9b0-c1d2-3e4f-5a6b-7c8d9e0f1a2b",
  "base_forecast_id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
  "name": "Black Friday Traffic Surge",
  "perturbed_prediction": 1285.40,
  "perturbed_probability": 0.96,
  "divergence": 0.5257,
  "perturbed_values": {
    "demand": 1348.0,
    "capacity": 631.8
  },
  "prediction_is_not_authorization": true,
  "executable_commands": []
}
```

---

### 6. Temporal Trends & Trajectory History (`/trends`)
Returns recent forecast trajectories, probability drift, and status across tracked targets.

- **Method**: `GET`
- **Path**: `/trends`

**cURL Example**:
```bash
curl -s "http://localhost:8000/trends?limit=5"
```

**Response Payload (HTTP 200 OK)**:
```json
{
  "status": "ok",
  "count": 2,
  "trends": [
    {
      "forecast_id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
      "target": "service:checkout:capacity_exceedance_24h",
      "prediction": 842.50,
      "range_lower": 810.20,
      "range_upper": 874.80,
      "probability": 0.88,
      "confidence": "high",
      "created_at": "2026-10-14T21:40:00+00:00",
      "status": "active"
    }
  ],
  "observed_at": "2026-10-14T21:40:05.123456+00:00"
}
```

---

## ⚙️ Environment Variables Specification

All configuration parameters can be configured through environment variables or a `.env` file:

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `APP_ENV` | String | `dev` | Environment mode (`dev`, `test`, `prod`). Requires production credential verification when `prod`. |
| `PORT` | Integer | `8000` | Port for the HTTP/ASGI server to bind. |
| `HOST` | String | `0.0.0.0` | Bind host address. |
| `DATABASE_URL` | String | `sqlite+aiosqlite:///./data/futuris.db` | SQLAlchemy database URL. Defaults to SQLite WAL file storage. |
| `OBJECT_STORE_PATH` | String | `./data/storage` | Local directory path for telemetry snapshots and evidence artifacts. |
| `LOG_LEVEL` | String | `INFO` | Python log level threshold (`DEBUG`, `INFO`, `WARNING`, `ERROR`). Structured format: `[%(asctime)s] [%(levelname)s] [futuris]: %(message)s`. |
| `API_KEYS_ENABLED` | Boolean | `true` | Enforces API key verification on protected endpoints. |
| `FUTURIS_API_KEY` | String | `None` | Master administrator API key for Futuris. |
| `FUTURIS_FRIDAY_API_KEY` | String | `None` | Shared API key for internal FRIDAY ecosystem communication. |
| `CORS_ORIGINS` | List[String] | `["https://friday-universe.duckdns.org", ...]` | Allowed CORS origin whitelist including public gateway and local development ports. |
| `SCHEDULER_ENABLED` | Boolean | `true` | Enables background APScheduler worker for forecast refresh and lifecycle sweeps. |
| `SELF_HEALING_ENABLED` | Boolean | `true` | Enables periodic self-assessment and self-healing supervisor loop. |
| `SELF_HEALING_INTERVAL_SECONDS` | Float | `60.0` | Cadence in seconds between self-healing health and schema checks. |
| `STARTUP_DEMO_SEED_ENABLED` | Boolean | `false` | Seeds synthetic demo forecasts on non-production startup if database is empty. |
| `FRIDAY_URL` | String | `http://friday:10000` | Mesh URL for primary FRIDAY Core gateway. |
| `INFERENCE_URL` | String | `http://inference:8000` | Mesh URL for live Inference Gateway. |
| `MEMORA_URL` | String | `http://memora:8000` | Mesh URL for Memora memory container. |
| `STRATEX_URL` | String | `http://stratex:8000` | Mesh URL for Stratex trading execution container. |
| `INTELX_URL` | String | `http://intelx:8000` | Mesh URL for IntelX research container. |
| `CORTEX_URL` | String | `http://cortex:8000` | Mesh URL for Cortex neural memory container. |
| `FORGE_URL` | String | `http://forge:8000` | Mesh URL for Forge code synthesis container. |
| `SENTINEL_URL` | String | `http://sentinel:8000` | Mesh URL for Sentinel security container. |

---

## 🐳 Docker Deployment & Container Hardening

The production `Dockerfile` uses a hardened multi-stage build:

- **Base Image**: `python:3.11-slim`
- **Security**: Runs under unprivileged user `appuser` (`UID 1000`, `GID 1000`).
- **Init Process**: Uses `tini` as entrypoint for clean PID 1 signal forwarding and zombie process reaping.
- **Healthcheck**: Built-in 30s Docker health check (`CMD curl -f http://localhost:8000/health || exit 1`).

### Quick Start with Docker Compose
```bash
# Build and run futuris attached to the friday_mesh network
docker compose up -d --build

# Inspect logs
docker compose logs -f futuris
```

---

## 🛠️ CLI Operations

FUTURIS includes a full-featured CLI for local management:

```bash
# Ingest synthetic telemetry
python -m futuris.cli ingest --days 180 --seed 42

# Generate forecast via CLI
python -m futuris.cli forecast --target service:checkout:capacity_exceedance_24h --horizon 24h

# Run rolling walk-forward backtest
python -m futuris.cli backtest --days 30 --stride 24

# Execute single lifecycle resolution sweep
python -m futuris.cli sweep

# Seed demo dataset
python -m futuris.cli demo

# Run production server
python -m futuris.cli serve --host 0.0.0.0 --port 8000
```