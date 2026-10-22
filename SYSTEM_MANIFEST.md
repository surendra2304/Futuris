# 🏛️ SYSTEM MANIFEST — Futuris Predictive Intelligence Engine

> **Official Subsystem Name:** Futuris  
> **Role in Ecosystem:** Calibrated Probabilistic Forecasting, Market Regime Transitions & Brier Calibration  
> **Repository:** [surendra2304/Futuris](https://github.com/surendra2304/Futuris) (Branch: main)  
> **Workspace Path:** d:\FRIDAY Universe\Futuris  
> **Hardware Target:** Oracle Cloud Always Free (2 OCPUs, 12 GB RAM Ampere A1 ARM64 / AMD x86_64 host)  
> **Docker Network:** `friday_mesh`  

---

## ☁️ 1. Configured Service Topology

| Attribute | Repository Configuration |
| :--- | :--- |
| **Public Gateway Domain** | `https://friday-universe.duckdns.org` |
| **Public Gateway Route** | `https://friday-universe.duckdns.org/futuris/*` |
| **Internal Container Port** | `8000` (Host Port: `8003`) |
| **Docker Mesh URL** | `http://futuris:8000` |
| **Health Check Endpoint** | `http://futuris:8000/health` / `https://friday-universe.duckdns.org/futuris/health` |
| **API Key Variable** | `FUTURIS_API_KEY` (configured in secret environment) |
| **Authentication Header** | `Authorization: Bearer <API_KEY>` or `X-API-Key: <API_KEY>` |
| **Database Architecture** | Local SQLite 3 with WAL Mode (`futuris.db`) |
| **Database Connection** | `sqlite+aiosqlite:///./data/futuris.db` |
| **Reverse Proxy** | Caddy reverse proxy on Oracle Cloud host with automated TLS |

---

## 🎯 2. Purpose & Responsibilities

### What Futuris IS:
* Futuris is a statistical forecasting and probabilistic calibration platform. It fits ARIMA, StatsForecast, and Monte Carlo distribution curves, computes Brier accuracy calibration, models market regime transitions, and feeds forecast distributions into Stratex and FRIDAY.

### What Futuris DOES:
* Operates as the **Calibrated Probabilistic Forecasting, Market Regime Transitions & Brier Calibration** specialist within the 9-agent FRIDAY Universe.
* Communicates directly with peer agents via authenticated REST within the private `friday_mesh` network.
* Persists temporal models, scenarios, and calibration outcomes to local SQLite (`futuris.db`) in WAL mode.

---

## 🌐 3. Ecosystem Endpoint Configuration (`friday_mesh`)

```env
# ============================================================================== #
#            FRIDAY UNIVERSE - ORACLE CLOUD & CONTAINER MESH GATEWAYS            #
# ============================================================================== #
# Public Gateway: https://friday-universe.duckdns.org
# Public Route:   https://friday-universe.duckdns.org/futuris/*
# Docker Network: friday_mesh (Internal Port: 8000, Host Port: 8003)

FUTURIS_URL=http://futuris:8000

# 1. ⚡ Inference AI Multi-Model Gateway
INFERENCE_URL=http://inference:8000
INFERENCE_API_KEY=<configure in secret environment>

# 2. 🧠 Memora Cloud Persistent Memory
MEMORA_URL=http://memora:8000
MEMORA_API_KEY=<configure in secret environment>

# 3. 📈 Stratex 24/7 Algorithmic Trading Platform
STRATEX_URL=http://stratex:8000
STRATEX_API_KEY=<configure in secret environment>

# 4. 🧠 IntelX Evidence & Intelligence Research Engine
INTELX_URL=http://intelx:8000
INTELX_API_KEY=<configure in secret environment>

# 5. 🔮 Futuris Calibrated Predictive Forecasting Engine
FUTURIS_URL=http://futuris:8000
FUTURIS_API_KEY=<configure in secret environment>

# 6. 🌐 Cortex Autonomous Web Operations & Intelligence
CORTEX_URL=http://cortex:8000
CORTEX_API_KEY=<configure in secret environment>

# 7. 🛠️ Forge Local Software Engineering Engine
FORGE_URL=http://forge:8000
FORGE_API_KEY=<configure in secret environment>

# 8. 🛡️ Sentinel Local Cybersecurity & Threat Defense Shield
SENTINEL_URL=http://sentinel:8000
SENTINEL_API_KEY=<configure in secret environment>

# 9. 🤖 FRIDAY Central Desktop Operating System
FRIDAY_URL=http://friday:10000
FRIDAY_API_KEY=<configure in secret environment>
```

---

## 🤖 4. Repository Guide

When opening this repository:
* **Identity:** You are working inside **Futuris** (`d:\FRIDAY Universe\Futuris`).
* **Configured Mesh URL:** `http://futuris:8000` (Host port: `8003`).
* **Public Route:** `https://friday-universe.duckdns.org/futuris/*`.
* **Authentication:** Incoming requests use `FUTURIS_API_KEY`.
* **Never Fake Tests:** All tests and verifications must be executed against real code and real endpoints.
