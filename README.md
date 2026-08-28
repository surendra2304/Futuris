# FUTURIS 🔮

**Calibrated Predictive Intelligence & Multi-Horizon Forecasting Platform**

FUTURIS is a standalone predictive intelligence layer engineered to produce rigorous, calibrated forecasts, multi-branch scenario trees, uncertainty estimates, and real-time decision support.

---

## 🏛️ Module Architecture

The codebase is organized into domain-isolated modules:

`	ext
futuris/
├── core/         # Domain models, pipeline orchestration, lifecycle control
├── models/       # Forecaster adapters, baselines, probabilistic models, routing
├── features/     # Feature engineering, temporal transforms, lags, embeddings
├── scenarios/    # Scenario graph definitions, counterfactuals, Monte Carlo engines
├── agents/       # Specialized forecasting agents, reasoning loops, consensus
├── evidence/     # Provenance tracking, source trust scoring, data snapshots
├── evaluation/   # Benchmark suites, calibration metrics, backtesting, drift detection
├── connectors/   # External data ingest adapters, streaming feeds
├── api/          # REST API endpoints, event contracts, request schemas
├── storage/      # Persistence layer, repositories, object storage abstractions
├── infra/        # Configuration (pydantic-settings), structured logging, scheduler
└── ui/           # Frontend dashboard (React + Vite + TypeScript placeholder)
`

---

## 🚀 Development Phases

1. **Phase 1: Project Scaffold** (Current) — Tech stack setup, environment config, structured JSON logging, container wiring, and CI.
2. **Phase 2: Core Domain & Storage** — Async SQLAlchemy 2.0 schemas, Alembic migrations, base entity models.
3. **Phase 3: Connectors & Ingestion** — Input adapters, streaming connectors, schema validation.
4. **Phase 4: Feature Pipeline** — Temporal transformations, lagging, calendar features.
5. **Phase 5: Baseline Forecasters** — Classical statistical & ML forecasting adapters.
6. **Phase 6: Probabilistic Modeling** — Quantile regression, conformal prediction, interval calibration.
7. **Phase 7: Scenario Graph & Simulation** — Counterfactual branching, graph traversal, Monte Carlo simulations.
8. **Phase 8: Evidence & Provenance** — Source trust scoring, verifiable snapshotting.
9. **Phase 9: Forecasting Agents** — Multi-agent consensus, LLM-augmented debate loops.
10. **Phase 10: Evaluation & Drift Engine** — Rolling-origin backtesting, calibration metrics, distribution drift.
11. **Phase 11: Scheduler & Orchestration** — APScheduler recurring pipelines, background workers.
12. **Phase 12: REST API & Event Contracts** — Comprehensive API routes, webhooks, auth guards.
13. **Phase 13: UI & Visualizations** — Interactive React dashboard, scenario tree explorer.
14. **Phase 14: Hardening & Security** — Rate limiting, cryptographic audit trails, performance profiling.
15. **Phase 15: Integration Benchmarking** — Multi-domain stress testing, synthetic dataset evaluations.
16. **Phase 16: Production Packaging** — Deployment templates, observability dashboards, full release.

---

## 🛠️ Quick Start

### Local Installation
`ash
make install
`

### Run Tests & Linters
`ash
make test
make lint
`

### Run API Server
`ash
make run
`

### Docker Compose
`ash
docker compose up -d postgres
`

---

## 📖 Engineering Diary
All daily progress and architectural decisions are tracked in [FUTURIS_DIARY.md](FUTURIS_DIARY.md) and the [diary/](diary/) directory.