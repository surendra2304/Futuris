# FUTURIS — Operational Capacity & Predictive Intelligence Platform

FUTURIS is a production-grade, standalone forecasting and predictive-intelligence platform. It treats honest calibration, immutable evidence provenance, and decision boundaries as core architectural invariants.

> **Prediction $\neq$ Authorization**: FUTURIS produces calibrated predictive distributions and advisory decision support, but never executes mitigations without explicit governance authorization.

---

## ⚡ Quickstart (5 Minutes)

Refer to the complete [**Platform Runbook**](scripts/runbook.md) for detailed operational instructions.

```bash
# 1. Start database
docker compose up -d

# 2. Run schema migrations
python -m alembic upgrade head

# 3. Seed demo data (180 days telemetry, live forecast, scenarios, backtests)
python -m futuris.cli demo

# 4. Start API server & React workspace
python -m futuris.cli serve --port 8000
```
- **Dashboard UI**: [http://127.0.0.1:8000/ui](http://127.0.0.1:8000/ui)
- **OpenAPI Docs**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Metrics**: [http://127.0.0.1:8000/metrics](http://127.0.0.1:8000/metrics)

---

## 🏗️ Architecture & Subsystem Modules

```
futuris/
├── core/            # Domain schemas, enums, resolution rules, lifecycle, pipeline
├── storage/         # SQLAlchemy 2.0 async repositories, ORM models, alembic migrations
├── connectors/      # Base connectors, synthetic telemetry generation
├── evidence/        # Provenance tracking, source trust scoring, Parquet snapshots
├── features/        # Normalization, contextualization, driver analysis (lead/lag)
├── models/          # statsforecast adapters (AutoARIMA, AutoETS, Naive, Drift), routing
├── evaluation/      # Metrics, calibration analysis (shrinkage, conformal), backtesting
├── scenarios/       # Counterfactual specs, dependency DAGs, Monte Carlo simulations
├── agents/          # SignalAnalyst, CalibrationAnalyst, deterministic fallbacks
├── api/             # Versioned REST API (/v1), OpenAPI contracts, RFC 7807 errors
├── infra/           # APScheduler, RBAC auth, append-only audit, Prometheus metrics
├── ui/              # React + Vite + TypeScript + Tailwind workspace
└── demo/            # Deterministic bootstrapping and demo data seeder
```

---

## 🛡️ Security, Governance & Status

- **Phases 0–4 (Complete)**: Core engine, persistence repositories, synthetic telemetry connectors, feature engineering, statsforecast adapters, statistical calibration, lifecycle outcome resolution, counterfactual scenarios, driver extraction, minimal advisory agents, versioned REST API, autonomous scheduler, React UI, and RBAC governance.
- **Phases 5–6 (Queued)**: External platform ecosystem adapters (NEXUS, FRIDAY).