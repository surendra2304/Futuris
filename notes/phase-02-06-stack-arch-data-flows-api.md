# Phase 2–6 Notes — Stack, Architecture, Data, Core Flows, API

## Tech stack (verified by execution, venv at /tmp/futuris-venv)
Python 3.11.2. Resolved: fastapi 0.142.2, uvicorn 0.54.0, pydantic 2.13.5, pydantic-settings, sqlalchemy 2.1.3, aiosqlite 0.22.1, asyncpg 0.32.0, alembic 1.20.0, apscheduler 3.11.3, structlog 26.1.0, httpx 0.28.1, statsforecast 2.1.1, scikit-learn 1.9.1, pandas 2.3.3, numpy 2.4.6, scipy 1.17.1, pyarrow 25.0.1, prometheus-client, typer 0.27.3, rich 15.0.0. Dev: pytest 9.1.1, pytest-asyncio, ruff 0.16.10.
`pip-audit -r requirements.txt` → **"No known vulnerabilities found"** (2026-10-07, PyPI advisory DB).
Note: fastapi 0.142.2 is far newer than the `>=0.110` pin — it uses lazy `_IncludedRouter` (include_router defers materialization); route enumeration must walk `effective_candidates()`.
UI: react 18.3, vite 5.4, typescript 5.5, tailwind 3.4, recharts, lucide-react, react-router-dom (installed but UNUSED — no router in main.tsx).

## Architecture (modular monolith, single process)
Layers (all inside `futuris/` package, one FastAPI process):
- `api/` — FastAPI app, routers (11 routers), deps (DI), errors (envelope handlers)
- `core/` — domain schemas (Pydantic, extra=forbid, invariants), enums, engine, pipeline, lifecycle, resolution, decision, thresholds, hashing, universe_domains, universe_forecasting
- `features/` — normalize (grid/dedupe/quality report), contextualize (36 features, strict ≤ as_of), drivers (lead/lag correlation)
- `models/` — adapter protocol + statsforecast adapters (naive, seasonal_naive, drift, auto_ets, auto_arima, mean_ensemble), registry, routing (heuristics), selection (held-out backtest w/ budget), regime, forge_predictor, ai_universe_enhanced
- `evaluation/` — metrics (MAE/RMSE/MAPE/Brier/log-loss/ECE), calibration (reliability curve, shrinkage, conformal), confidence (meta-confidence rules), backtest (walk-forward + leakage validator), drift (3-sigma control limits)
- `evidence/` — snapshots (Parquet freeze, PII denylist, SHA-256, immutable), trust registry
- `scenarios/` — spec builders, DAG (default_ops_wedge), engine (linear + Monte Carlo, sensitivity, comparison)
- `agents/` — runner (LLM cost guardrail 50 calls/h), signal_analyst, calibration_analyst, protocol
- `storage/` — SQLAlchemy 2.0 async ORM (12 tables), repositories (append-only events), db (engine, pragmas, schema self-heal)
- `infra/` — config (pydantic-settings), auth (RBAC), audit (append-only log), events (emitter + webhooks + SSRF guard), scheduler (APScheduler), self_healing (supervisor), resilience (peer circuit breakers), cpu (run_cpu gate), metrics (Prometheus), llm, logging (structlog JSON), research_context
- `connectors/` — base, synthetic_telemetry (deterministic generator), nexus, forge, trading_bot, intelx_context
- `ecosystem/` — typed peer adapters (8 peers, circuit-broken)
- `integrations/` — friday_client SDK, memora client/publisher/consumer/cloud-fallback, sentinel_schema
- `upgrade/` — hardening layer: auth (PBKDF2 310k), safe_config (prod guard), quality (ForecastQualityGate), rate_limit (in-memory), scheduler (lease single-flight), idempotency, outbox, policy (SSRF/horizon), provider (circuit), retry, state (versioned state machine), persistence (DurableStateStore sqlite), decision, checkpoint, budget, cancellation, compat, context_firewall (prompt-injection patterns), forecast_guard, observability, tool_registry, agent_runtime, audit, models
- `demo/` — deterministic seeder, startup seed policy

Process boundaries: ONE process: uvicorn serving FastAPI; in-process APScheduler (4 jobs); self-healing asyncio loop (60s); Memora event consumer task (15s poll, only if MEMORA_API_KEY); optional startup demo seed task. External: 8 peer agents over HTTPS (IntelX, Inference, Memora, Stratex, Forge, Sentinel, Cortex, FRIDAY), LLM providers (anthropic/openai, default none).

## Entry points & startup (futuris/api/app.py, futuris/cli.py)
CLI (typer): `ingest`, `forecast`, `backtest`, `sweep`, `create-admin-key` (prints plaintext admin key), `serve` (uvicorn, PORT/HOST env override), `demo` (DemoSeeder 180d).
Lifespan sequence (app.py:49-140): ensure_schema() [SQLite: create_all + add_missing_columns + index repair; else verify only] → inject shared httpx.AsyncClient into global event_emitter → if not pytest and SCHEDULER_ENABLED: start ForecastScheduler (jobs: ingest 15m, forecast_refresh 60m, lifecycle_sweep 30m, backtest_nightly cron 02:00) → if not pytest and SELF_HEALING_ENABLED: self_healing_loop task (60s) → if not pytest and MEMORA_API_KEY: memora_event_worker task → if not pytest and should_seed_demo_on_startup (enabled flag AND env not prod/production) and DB empty: background seed after 2s (fast_mode, 7 days).
Scheduler is constructed with a dedicated long-lived session; jobs hardcode SyntheticTelemetryConnector(seed=42).
Health: GET /health = liveness + storage schema probe (degraded if schema missing); GET /metrics Prometheus; GET / → redirect to /ui for browsers.
Boot failure: schema incomplete → logged error, requests get 503 storage_schema_missing + one-shot background repair (schedule_schema_repair).

## Config inventory (futuris/infra/config.py + scattered)
APP_ENV (dev), DATABASE_URL (sqlite+aiosqlite:///./data/futuris.db), OBJECT_STORE_PATH (./data/storage), LLM_PROVIDER (none), LLM_API_KEY, LOG_LEVEL (INFO), FUTURIS_API_KEY (None), API_KEYS_ENABLED (True in code; .env.example says false — DISCREPANCY), SELF_HEALING_ENABLED (True), SELF_HEALING_INTERVAL_SECONDS (60), SCHEDULER_ENABLED (True), ALLOW_DEMO_CREDENTIALS (False), STARTUP_DEMO_SEED_ENABLED (False), INFERENCE_URL/INFERENCE_API_KEY, MEMORA_URL/MEMORA_API_KEY (AliasChoices: FUTURIS_MEMORA_API_KEY, FUTURIS_API_KEY, MEMORA_API_KEY — master key doubles as Memora key), STRATEX_URL/KEY, INTELX_URL/KEY, CORTEX_URL, FORGE_URL, SENTINEL_URL, FRIDAY_URL, FUTURIS_FRIDAY_API_KEY, PORT/HOST (cli serve), FUTURIS_CPU_SLOTS (infra/cpu.py), INTELX_WEBHOOK_API_KEY (routers/webhooks.py), AUTH_DISABLED (read via getattr but NOT a declared field; extra="ignore" → always False — dead code).
Import-time guard (config.py:165-172): if APP_ENV in {prod, production} → production_env_guard requires all 6 secrets (FUTURIS_API_KEY, INFERENCE_API_KEY, MEMORA_API_KEY, STRATEX_API_KEY, INTELX_API_KEY, FUTURIS_FRIDAY_API_KEY) present, ≥32 chars, non-placeholder; validate_credential_contract (environment="prod" hardcoded) requires master ≥32 + service keys ≥32, demo creds forbidden.

## Data model (storage/models.py, alembic/versions)
12 tables: forecasts (25 cols incl. predictive_distribution, intervals, calibration_metrics, model_metadata, idempotency_key, evidence_class, evidence_source; indexes ix_forecasts_target_as_of, ix_forecasts_status, ix_forecasts_idempotency_key, ix_forecasts_evidence_class), evidence_refs, outcomes (forecast_id UNIQUE), scenarios, forecast_events (append-only; forecast_id nullable for system events), intelx_notices (event_id PK string), observations (ix_observations_series_time), signal_sources, model_registry (model_version PK), evaluation_runs, api_keys (key_hash PK = SHA-256), audit_logs.
Migrations: 0001 initial (dialect-safe JSON/JSONB, batch_alter_table FK — runs on SQLite, VERIFIED), 0002 close schema drift (2026-10-05, adds 5 forecast columns + intelx_notices + observations renames), 0003 provenance columns (2026-10-06). tests/test_migrations.py runs real chain on SQLite and diffs against ORM metadata.
SQLite pragmas: WAL, synchronous=NORMAL, busy_timeout=30000, foreign_keys=ON. JSON via JSON().with_variant(JSONB, postgresql).
Flush-ordering pinned via relationships (ForecastEventModel.forecast, ForecastModel.scenario, EvaluationRunModel.model_registry — comments explain FK ordering).
Transactions: FastAPI session dependency commits at teardown (deps.py get_db_session) with pending-rollback guard; repositories flush within request transaction; scheduler uses own long-lived session (commit per job); demo seed single session; market router commits via request session. Multi-step writes (forecast+event, outcome+status+event) are single-transaction per request — good; scheduler jobs commit per job.

## Core flows (traced)
F1 POST /v1/forecasts (analyst): parse_horizon (bounded 1m–365d, overflow-safe) → ForecastEngine.orchestrate: connector.fetch (default SyntheticTelemetryConnector seed=42, 14d lookback) → IntelX research best-effort → run_cpu gate (semaphore CPU_SLOTS, exclusive) → normalize (dedupe, 5m grid, quality report) → contextualize (features ≤ as_of) → freeze Parquet snapshot (PII denylist {user_id,email,ip_address,ssn,credit_card,phone,customer_name,user_pii}, SHA-256, re-write raises SnapshotAlreadyExistsError) → ModelRouter candidates (heuristics on history/coverage) → select_best_adapter (held-out MAE; CHEAP={naive,drift,seasonal_naive}; budget/cpu skips recorded in metadata; fallback=naive if all fail) → refit → predict (intervals z90·σ·√step; exceedance prob = 1000 bootstrap sims, seed 42) → AIUniverseModelEnhancer interval adjustment → ConfidenceAssessor (4 rules) → DriverAnalyzer (lead/lag corr) → Forecast(DRAFT, invariants: range order, as_of<expires_at, review_at≥as_of, driver evidence_refs ⊆ evidence, prediction_is_not_authorization=True, executable_commands=[]) → route: ForecastQualityGate.require(envelope) → abstention gate (202 if required_confidence unmet) → repo.create (forecast + forecast_created event, one flush) → AuditLogger.log_mutation → 201. Measured live: ~7.7s wall (AutoARIMA-dominated).
F2 FRIDAY delegation POST /v1/friday/forecast & /v1/friday/delegate: verify_friday_auth (X-API-Key or Bearer; expected = FUTURIS_FRIDAY_API_KEY | FRIDAY_API_KEY | FUTURIS_API_KEY; fail-closed 503 if unset or <32 chars; hmac.compare_digest; InMemoryRateLimitBackend 100/h keyed friday:{key}) → idempotency (explicit key > X-Idempotency-Key > friday_req:{id}; replay cached) → caller telemetry checks (≥5 points, staleness ≤3600s unless allow_stale; else BLOCKED/INSUFFICIENT_DATA response with zeros) → research enrichment (1.5s bound) → ForecastingPipeline.run (same stages as F1) → persist with idempotency_key → audit → response (prediction_is_not_authorization=True, executable_commands=[], honest calibration metrics: uncalibrated when no outcomes). /delegate: forbidden action denylist (~35 actions + payload keys command/script/exec/...) → 403 "Prediction is not authorization"; routes forecast/scenario/resolve/cancel/calibration/status; unknown → 400. VERIFIED live: 403 on scale+command; 201 on forecast; 401 bad key.
F3 Universe predictions POST /v1/predictions/predict (analyst): get_target_spec (18 targets, 9 domains) → generate_universe_forecast: caller context values (point_estimate/current_value/probability/range_*) → echo labeled evidence_class=SYNTHETIC, source=caller_supplied_context, real content_hash, ±10% policy band recorded in assumptions; else pipeline (labeled synthetic_telemetry_generator); exception → INSUFFICIENT_DATA forecast (zeros + status, never placeholder) → persist + audit → risk level + interpretation. GET /v1/predictions/matrix (anonymous): latest active per target + refresh_missing_within_budget (15s, sequential, grace 2s, session rollback recovery) → domain rollup → health score = 100 − 25·crit − 10·high − 3·elev. POST /refresh-all (analyst) same with shared budget.
F4 Lifecycle: scheduler lifecycle_sweep_job (30m) / POST sweep CLI / LifecycleManager.run_lifecycle_sweep: active forecasts → capacity_events break assumptions → invalidate; now ≥ expires_at → resolve via CapacityExceedanceResolutionRuleV1 (window (as_of, expires_at], expected 5m steps, >20% gap → AMBIGUOUS outcome; else max(value) ≥ threshold(4000 default or snapshot capacity_limit) → event_occurred) → outcome + status RESOLVED + event; exception → EXPIRED (swallows error — documented). Manual: POST /v1/forecasts/{id}/resolve-manual (admin, 409 on duplicate — VERIFIED live, audit-logged). Invalidate: POST /{id}/invalidate (admin, reason min 3 chars, audit). point_in_time_query replays events to reconstruct status at t (fixed H2).
F5 Market POST /v1/futuris/forecast (aliases /api/v1/futuris/forecast, /v1/market/forecast; GET twin anonymous): IntelX reports + durable notices → TradingBotConnector telemetry (volatility+drawdown REQUIRED else 503 "no placeholder values" — verified design) → heuristic formulas: vol_prob=clip(vol·0.70·mult, .12, .92), dd_prob=clip(dd/15·factor, .08, .85), regime rules (sentiment/vol thresholds) → Inference grounding best-effort → persist Forecast (evidence_class=LIVE, content_hash over telemetry observations, inline:// snapshot path) + audit → publish_forecast_advisory to Memora (HMAC-SHA256 envelope, 2 attempts, only active+current forecasts) → dispatch to Stratex → response. GET /v1/market/accuracy (anonymous): computed from resolved outcomes; insufficient_data when empty (no fabricated numbers — C3 fixed, VERIFIED live).
F6 Scenarios POST /v1/forecasts/{id}/scenarios[/compare] (analyst): ScenarioEngine over DependencyGraph.default_ops_wedge (demand→utilization→latency/error_rate→revenue, capacity→utilization); Monte Carlo (default 1000 samples) or linear; per-override sensitivity ranking; compare → divergence matrix; scenario record persisted (NOT audit-logged — gap vs SECURITY.md §4).
F7 Webhooks: POST /v1/webhooks (analyst): assert_safe_webhook_url (https only, no credentials in URL, blocked hostnames localhost/metadata..., DNS-resolved public IP check — private/loopback/link-local/multicast/reserved/unspecified rejected) → in-memory subscription, secret `whsec_{uuid}` returned once. EventEmitter._deliver: HMAC-SHA256 X-Futuris-Signature, 3 attempts, backoff 0.05·2^n, retries on transport/429/5xx, no retry on other 4xx, WEBHOOK_DELIVERY_TOTAL metrics. Subscriptions are in-memory only (lost on restart). Inbound POST /v1/webhooks/research-finding-relevant: shared-secret auth (INTELX_WEBHOOK_API_KEY | FUTURIS_FRIDAY_API_KEY | FUTURIS_API_KEY; 503 if unset/<32; hmac.compare_digest) → durable store first (IntelXNoticeModel, idempotent by run_id, 409 on content conflict) → optional reforecast for BTCUSDT/ETHUSDT/SOLUSDT targets only (never infer symbol from prose) → background task → 200.

## API surface (63 operations / 52 unique paths, enumerated live)
Auth: X-API-Key header (Bearer prefix stripped) → SHA-256 → api_keys table (revoked excluded) or master FUTURIS_API_KEY (== compare, non-constant-time) or ANONYMOUS_VIEWER (role anonymous=0). Roles: viewer≥1 (events, self/*), analyst≥2 (create/predict/refresh/scenarios/webhooks/seed? no — seed is admin, market POST, ecosystem seed admin), admin≥3 (invalidate, resolve-manual, audit, ecosystem/seed). AllowAnonymousRead: GET /v1/forecasts, /{id}, /{id}/outcome, /v1/models, /v1/evaluation/*, /v1/predictions/matrix, /v1/market/* GET, /v1/ecosystem/peers. FRIDAY routes: verify_friday_auth (separate shared secret). /v1/task/execute + /api/v1/task/execute: NO auth by design — 403 for forbidden actions, 501 otherwise.
Duplicate mounts (attack-surface + OpenAPI duplication): friday router at /v1/friday + /api/v1/friday (wait — actually mounted at /v1/friday via router prefix and /api via include prefix → /api/v1/friday/*); market router at /v1/futuris + /api/v1/futuris + /v1/market; webhooks at /v1 + /api/v1.
Error envelope: {"error": {code, message, details}}; FuturisAPIError, 422 sanitised (bytes→str), IntegrityError→409, OperationalError→503 (storage_schema_missing/storage_unavailable/storage_busy/storage_error + auto repair), HTTPException map, unhandled→500 with str(exc)[:300] in details (INFO DISCLOSURE).
Pagination: list_forecasts loads ALL rows then slices in Python (limit/offset NOT a resource bound — forecasts.py:208-255); X-Total-Count header.

## Endpoint table (method path — auth — handler)
GET / — public — app.py root
GET /health — public — app.py health_check (liveness+storage probe)
GET /metrics — public — app.py get_metrics (Prometheus)
GET /docs /redoc /openapi.json — public (FastAPI default)
POST /v1/forecasts — analyst — forecasts.py:create_forecast
GET /v1/forecasts — anon-read — forecasts.py:list_forecasts (in-Python pagination)
GET /v1/forecasts/{id} — anon-read — get_forecast
POST /v1/forecasts/{id}/invalidate — admin — invalidate_forecast
GET /v1/forecasts/{id}/outcome — anon-read — get_forecast_outcome
POST /v1/forecasts/{id}/resolve-manual + /v1/forecasts/outcomes/{id}/resolve-manual — admin — resolve_manual (409 dup)
POST /v1/forecasts/{id}/scenarios + /compare — analyst — scenarios.py
GET /v1/evaluation/calibration — anon-read — evaluation.py (persisted pairs, insufficient_data when empty)
GET /v1/evaluation/backtests + /{run_id} — anon-read — persisted evaluation_runs
GET /v1/events — viewer — events.py list_events
POST /v1/webhooks — analyst — events.py create_webhook (SSRF guard)
DELETE /v1/webhooks/{id} — analyst — delete_webhook (in-memory)
POST /v1/webhooks/research-finding-relevant — shared secret — webhooks.py (IntelX inbound)
GET /v1/models — anon-read — models.py (registry + persisted benchmark status)
GET /v1/audit — admin — audit.py (append-only log)
POST /v1/friday/forecast — friday-key — friday.py delegate_forecast (idempotent)
POST /v1/friday/delegate — friday-key — delegate_task (403 forbidden actions)
POST /v1/friday/scenario — friday-key — evaluate_scenario
POST /v1/friday/forecasts/{id}/cancel + /resolve, POST /v1/friday/resolution — friday-key
GET /v1/friday/forecasts, /v1/friday/calibration — friday-key
POST /v1/futuris/forecast (alias /v1/market/forecast) — analyst — market.py post_market_forecast
GET /v1/futuris/forecast, /v1/market/forecast — anon-read — get_market_forecast (runs full pipeline anonymously!)
GET /v1/futuris/accuracy, /v1/market/accuracy — anon-read — computed from outcomes
GET /v1/ecosystem/peers — anon-read — ecosystem.py (probes 8 peers over HTTPS)
POST /v1/ecosystem/seed — admin — single-flight background DemoSeeder
POST /v1/predictions/predict — analyst — predictions.py (caller-context echo or pipeline; honest insufficient_data)
GET /v1/predictions/matrix — anon-read — build_universe_matrix (15s backfill budget)
POST /v1/predictions/refresh-all — analyst — refresh_all_within_budget
GET /v1/self/status, /capabilities, /peers — viewer — self_status.py (measured self-model)
POST /v1/self/heal, /v1/self/peers/{peer}/reset — analyst
POST /v1/task/execute (+/api/v1) — none — app.py execute_task (403/501 fail-closed)
