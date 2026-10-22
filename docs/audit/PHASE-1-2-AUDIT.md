# FUTURIS — Phase 0/1/2 Audit (Truth Audit + Bug Hunt)

Auditor: Arena agent (acting principal engineer)
Date: 2026-10-05
Repository state: `arena/01a10cc5-futuris` @ `a86c3f4`
Environment: Python 3.11.2, fresh `pip install -e ".[dev]"`, SQLite (`sqlite+aiosqlite`), no Docker available in sandbox.

Every claim below is backed by a command that was actually executed; raw evidence is quoted or summarised inline.

---

## PHASE 0 — TOTAL COMPREHENSION

### (a) What this project IS

FUTURIS is a standalone **calibrated forecasting / predictive-intelligence platform** for the FRIDAY Universe agent fleet
(~20.8k LOC Python + a React/Vite console). Its load-bearing architectural claim is stated in README and SECURITY.md:
*honest calibration, immutable evidence provenance, and a hard boundary between prediction and authorization*
(`prediction_is_not_authorization` is enforced as a Pydantic invariant on every forecast).

The **dream state** is legible from the code, the README, and especially the UI: a service where every number it serves is
provenance-verified (live telemetry vs synthetic/demo) so the console can display calibrated predictions that FRIDAY/Stratex/
Sentinel can *trust but not execute*. `futuris/ui/src/App.tsx` is the clearest statement of intent — the console deliberately
**withholds forecast values** and says: *"This console will show values only after the API provides verifiable input provenance."*

### (b) Request/execution flow (real names)

1. **HTTP entry** — `futuris/api/app.py:app` (FastAPI). Middleware `RequestIdMiddleware`; error envelopes via
   `futuris/api/errors.py:register_error_handlers`. Routers mounted under `/v1` (+ duplicate `/api`, `/api/v1`, `/v1/futuris`, `/v1/market`).
2. **Auth** — `futuris/infra/auth.py:get_current_user` resolves `X-API-Key` → `AuthUser` (viewer/analyst/admin);
   `require_viewer` / `require_analyst` / `require_admin`. FRIDAY traffic uses `futuris/api/routers/friday.py:verify_friday_auth`
   (hmac.compare_digest against `FUTURIS_FRIDAY_API_KEY`, ≥32 chars, 100 req/h via `InMemoryRateLimitBackend`).
3. **Forecast creation** — `POST /v1/forecasts` → `ForecastEngine.orchestrate` (`futuris/core/engine.py`) **or**
   `ForecastingPipeline.run` (`futuris/core/pipeline.py`) for FRIDAY. Pipeline stages:
   `IngestionStage` → `NormalizationStage` (`features/normalize.py:Normalizer`) → `ContextualizationStage`
   (`features/contextualize.py:ContextLayer`, 36 features, strict `<= as_of`) → `ModelingStage`
   (`models/registry.py:model_registry`, `models/routing.py:ModelRouter`, adapters in `models/adapters.py`,
   held-out MAE selection) → `CalibrationDecisionStage` (`evaluation/confidence.py:ConfidenceAssessor`,
   `features/drivers.py:DriverAnalyzer`, `core/decision.py:DecisionSupport`).
4. **Evidence freezing** — `evidence/snapshots.py:EvidenceSnapshotter.freeze_snapshot` writes an immutable Parquet slice
   (SHA-256 content hash, PII denylist) and returns `EvidenceRef`.
5. **Quality gate** — `upgrade/quality.py:ForecastQualityGate.require` (finite numbers, interval order, evidence present,
   confidence in [0,1]) is invoked on the API path.
6. **Persistence** — `storage/repositories.py:ForecastRepository.create` writes `ForecastModel` + `EvidenceRefModel`
   (+ a `forecast_created` `ForecastEventModel`); `storage/db.py:async_session_factory`; migrations in `alembic/versions/0001_initial_schema.py`.
7. **Lifecycle** — `core/lifecycle.py:LifecycleManager.run_lifecycle_sweep` resolves/expires/invalidates; ground truth via
   `core/resolution.py:CapacityExceedanceResolutionRuleV1`; outcomes via `OutcomeRepository`; events via `infra/events.py:event_emitter`.
8. **Background** — `infra/scheduler.py:ForecastScheduler` (APScheduler jobs: ingest/refresh/lifecycle/nightly backtest) and
   `integrations/memora_event_consumer.py:memora_event_worker` (started by lifespan only when `MEMORA_API_KEY` is set).
9. **UI** — `futuris/ui/src/*` (React + Vite, `base: /ui/`), served by `SPAStaticFiles` from `futuris/ui/dist`.

### (c) The five most important files

| File | Why |
|---|---|
| `futuris/core/engine.py` (+ `core/pipeline.py`) | The forecasting core: ingestion → features → model selection → calibration → drivers → Forecast assembly. Everything downstream consumes this. |
| `futuris/core/schemas.py` | The domain contract: `Forecast` invariants (`prediction_is_not_authorization`, interval order, temporal order, driver↔evidence integrity). The product's promises live here as code. |
| `futuris/api/routers/{forecasts,friday,market,predictions,evaluation}.py` | The served surface (and where most trust/provenance problems live). |
| `futuris/storage/repositories.py` + `storage/models.py` | Persistence + append-only audit semantics; the PIT-query and audit-integrity behaviour. |
| `futuris/upgrade/quality.py` + `upgrade/forecast_guard.py` | The "hardening" gates (quality, point-in-time, source policy) that are supposed to make the platform trustworthy. |

### (d) What surprised me

1. **The honesty gap is inverted.** The UI refuses to display unverified numbers, but the API *serves hardcoded numbers*
   (`/v1/market/accuracy`, `/v1/evaluation/backtests`, the whole `/v1/predictions/*` universe matrix) with fabricated
   evidence hashes. The front end is more honest than the back end.
2. 4,905 `node_modules` files are committed, and they're an **incomplete** install — a fresh clone cannot build the UI.
3. The documented quickstart (`alembic upgrade head`) **destroys the SQLite schema** (proved below).
4. The project's own E2E proof script cannot pass in a clean environment.
5. CI is written to hide the lint backlog (`continue-on-error: true`) while the diary claims "0 lint warnings".

---

## PHASE 1 — TRUTH AUDIT (raw results)

### 1. Install / lint / types / tests

```
$ pip install --break-system-packages --user -e ".[dev]"      # fresh install
Successfully installed ... fastapi-0.142.2 pytest-9.1.1 sqlalchemy-2.1.3 statsforecast-2.1.1 ...

$ python -m pytest
171 passed, 1 warning in 215.35s (0:03:35)

$ python -m ruff check .
Found 478 errors.   (220 E501, 73 F401, 61 T201, 33 I001, 31 ARG001, ...)
ruff 0.16.10 ; pyproject selects ["E","F","I","N","W","UP","B","A","C4","T20","RET","SIM","ARG"]

$ python -m pytest --collect-only | tail -1
171 tests collected in 1.64s
```

There is **no type checker configured** (no mypy/pyright in `pyproject.toml`, `Makefile`, or CI).

### 2. Boot + exercise the main flows

Boot (ASGI TestClient, fresh SQLite, real routes, `raise_server_exceptions=False`):

```
/health -> 200      / -> 200        /docs -> 200     /openapi.json -> 200
/metrics -> 200     /ui/ -> 200     /v1/events -> 200
/v1/models -> 500   {"error":{"code":"internal_server_error", ... "'ModelRegistry' object has no attribute 'list_models'"}}
/v1/audit -> 403    /v1/forecasts -> 200    /v1/evaluation/calibration -> 200
/v1/evaluation/backtests -> 200     /v1/market/accuracy -> 200
/v1/ecosystem/peers -> 200          /v1/predictions/matrix -> 200
```

Keyed probe (`scripts/_probe_tmp.py`, `API_KEYS_ENABLED=true`, master + FRIDAY keys) — verbatim result table:

```
GET  /v1/models (anonymous)            -> 500
GET  /v1/models (auth)                 -> 500
POST /v1/webhooks (anonymous)          -> 201  (returns whsec_... secret, url=https://evil.example/x)
POST /v1/webhooks (auth)               -> 201
POST /v1/forecasts (analyst)           -> 201  forecast_id=cea515ea-...
POST /v1/forecasts (anonymous)         -> 403
POST /v1/forecasts (bogus key)         -> 401
GET  /v1/forecasts/{id}                -> 200
GET  /v1/forecasts/{id}/outcome        -> 404
POST /v1/friday/forecast (no key)      -> 401
POST /v1/friday/forecast (friday key)  -> 201
POST /v1/friday/delegate (friday key)  -> 200 status=SUCCESS
POST /v1/friday/delegate execute rm -rf / (friday key) -> 403 "Prediction is not authorization"
POST /v1/predictions/predict (anon)    -> 200
POST /v1/predictions/refresh-all (anon)-> 200
GET  /v1/events (anon)                 -> 200
GET  /v1/evaluation/calibration (anon) -> 200
GET  /v1/evaluation/backtests (anon)   -> 200
GET  /v1/audit (auth admin)            -> 200
GET  /v1/market/accuracy (anon)        -> 200
POST /v1/ecosystem/seed (anon)         -> 200 (starts a 180-day synthetic seed in the background)
```

Fabricated payloads actually returned (verbatim excerpts):

```json
GET /v1/market/accuracy ->
{"total_evaluated":48,"accuracy_pct":89.58,"brier_score":0.042,"status":"ACTIVE",
 "recent_records":[{"symbol":"BTCUSDT","prediction_correct":true,"metric":"volatility_range_contained"}, ...]}

GET /v1/evaluation/backtests ->
[{"run_id":"8f88c880-5a33-4f24-9b2f-744ac5fff5cd","target":"service:checkout:capacity_exceedance_24h",
  "stride_hours":24,"horizon":"24h","total_forecasts":30,"mae":45.2,"coverage_90":0.89,
  "created_at":"2026-10-04T16:04:39Z"}]

GET /v1/evaluation/calibration ->   (no outcomes existed; hardcoded fallback sample was used)
{"bin_counts":[0,0,0,0,0,30,0,0,0,0],"expected_calibration_error":0.5167,"sample_count":30,
 "calibration_method":"empirical_binned","data_freshness":"live_persisted"}
```

Webhook delivery probe (MockTransport returning HTTP 500):

```
HTTP attempts on a 500 response (documented 'max 3 attempts'): 1
global event_emitter has http client: False
```

Audit coverage probe (create → invalidate → resolve-manual with a master key):

```
create: 201 ; invalidate: 200 ; resolve: 200 ; audit rows: 0
```

Migration probe (README step 2, default SQLite URL):

```
$ DATABASE_URL=sqlite+aiosqlite:////tmp/alembictest/mig.db python -m alembic upgrade head
NotImplementedError: No support for ALTER of constraints in SQLite dialect.
Tables created: ['alembic_version', 'forecasts', 'scenarios']      # migration aborted part-way
$ SELECT forecast_id, predictive_distribution FROM forecasts LIMIT 1
OperationalError: no such column: predictive_distribution          # app is now unusable
```

Point-in-time query probe:

```
actual status in DB: invalidated
point_in_time_query status: active
events: [('forecast_created','service:checkout:capacity_exceedance_24h',None),
         ('forecast_updated', None, 'invalidated')]
```

Duplicate resolution probe:

```
resolve #1: 200
resolve #2 (duplicate): 500  {"detail":"...(sqlite3.IntegrityError) UNIQUE constraint failed: outcomes.forecast_id..."}
```

Project E2E script (its own verification harness):

```
$ DATABASE_URL=... python scripts/e2e_system_test.py
[STEP 13] REST API Integration -> [FAIL] TypeError: Header value must be str or bytes, not <class 'NoneType'>
SUMMARY: 12 PASSED | 1 FAILED      (FUTURIS_DIARY.md Day 8 claims "13/13 subsystems passed")
```

UI build (fresh `npm ci` in an isolated copy — the working tree's `node_modules` is a broken partial commit):

```
$ npm ci            -> added 177 packages in 4s
$ npx tsc --noEmit  -> exit 0
$ npx vite build    -> ✓ built in 1.95s (dist/assets/index-DiqNAz4q.js, index-eZ_yEUY8.css)
$ node futuris/ui/node_modules/typescript/bin/tsc -b
Error: Cannot find module '../lib/tsc.js'          # committed node_modules is incomplete
```

### 3. Docs vs reality

| Claim (source) | Reality |
|---|---|
| "**82 passed** (100% green pass rate with 0 linting warnings)" — `FUTURIS_DIARY.md`, `AUDIT_REPORT.md` | 171 passed; `ruff` reports **478 errors**. CI hides them with `continue-on-error: true`. |
| "13/13 subsystems passed" — diary Day 8 | 12/13; Step 13 crashes on unset `FUTURIS_API_KEY`. |
| README quickstart: `docker compose up -d` → `alembic upgrade head` → `cli demo` → `cli serve` | Step 2 crashes on the default SQLite URL and leaves a half-migrated DB. |
| SECURITY.md §4: "Every mutating API action logs the actor identity … SHA-256 payload hash" | Standard mutation routes log nothing (`/v1/audit` → 0 rows after create/invalidate/resolve). |
| docs/events.md: webhook delivery with "Retry with exponential backoff (max 3 attempts)" | No delivery at all in the served app (no HTTP client); no retries even when a client is injected. |
| README: "Phases 0–4 (Complete) … Phases 5–6 (Queued): external platform adapters (NEXUS, FRIDAY)" | NEXUS/FRIDAY/market adapters already exist; the queue note is stale. |
| README/SECURITY.md: "honest calibration, immutable evidence provenance" | `/v1/predictions/*`, `/v1/market/accuracy`, `/v1/evaluation/*` return hardcoded numbers with `content_hash = e3b0c44…` (SHA-256 of the empty string). |
| `futuris/integrations/friday_client.py` docstring: `ScenarioSpec.stress_spec(demand_multiplier=1.4, capacity_override=3200)` | No such method; the real API is `ScenarioSpec.stress(demand_multiplier, capacity_multiplier, name)`. |

**Working but undocumented:** the fail-closed FRIDAY auth (503 when unconfigured), the PBKDF2 `CredentialHasher`,
`ForecastQualityGate` invariants, append-only `EventRepository` (raises `ReadOnlyAuditViolationError` on update/delete),
immutable snapshot re-write protection (`SnapshotAlreadyExistsError`), PII denylist, the `/v1/predictions` refresh budget
(`REFRESH_BUDGET_SECONDS = 15`), and the `prediction_is_not_authorization` 403 guard.

### 4. Test safety net

A net exists (171 tests, ~2.3k assertions) but it **does not cover** any of the following: `/v1/models`,
`point_in_time_query` correctness, webhook delivery, audit coverage of mutation routes, alembic migrations,
the universe/market/evaluation honesty problem, or FRIDAY auth failure modes. Several tests
(e.g. `tests/test_universe_predictions.py:80` asserting `point_prediction == 45.0`) lock in the hardcoded generator.

---

## PHASE 2 — PRIORITISED BUG REPORT

### CRITICAL

**C1 — `GET /v1/models` returns HTTP 500 (dead route).**
*File:* `futuris/api/routers/models.py:26` → `model_registry.list_models()`; `futuris/models/registry.py` (no `list_models`).
*Root cause:* the router calls a registry method that was never implemented (`ModelRegistry` stores `_adapters` and exposes `current_active()`).
*Evidence:* boot probe `500 {"code":"internal_server_error","details":"'ModelRegistry' object has no attribute 'list_models'"}`.
*Fix:* add `list_models()` to `ModelRegistry`; make the route require viewer; stop returning hardcoded `benchmark_scores` (read `EvaluationRepository` or label unavailable). Regression test on `GET /v1/models`.

**C2 — Documented migration path corrupts the default database.**
*File:* `alembic/versions/0001_initial_schema.py:64` (`op.create_foreign_key`), plus unconditional `postgresql.JSONB(...)` columns and an ORM/migration schema drift (missing `drivers`, `predictive_distribution`, `intervals`, `calibration_metrics`, `model_metadata`, `idempotency_key`, `intelx_notices`…).
*Root cause:* SQLite cannot ALTER constraints without batch mode; the migration was written/tested for Postgres only while the default `DATABASE_URL` is SQLite; `Base.metadata.create_all` then cannot repair existing tables.
*Evidence:* `NotImplementedError: No support for ALTER of constraints in SQLite dialect`; afterwards `no such column: predictive_distribution`.
*Fix:* dialect-safe migration (`sa.JSON().with_variant(JSONB,"postgresql")`, `batch_alter_table`) + a new migration adding the missing columns/tables + a test that runs `alembic upgrade head` on SQLite and performs a full repository round-trip. Fail loudly on schema drift.

**C3 — Fabricated data served as real intelligence (violates the project's core invariant).**
*Files/lines:* `futuris/api/routers/evaluation.py:69-104` (hardcoded backtest run 45.2 / 0.89 and report), `:48-52` (hardcoded calibration fallback sample), `futuris/api/routers/market.py:401-410` (hardcoded `/accuracy` 48 / 89.58 % / 0.042 + fabricated records), `futuris/core/universe_forecasting.py:60-190` (per-target hardcoded probabilities, ranges, and driver strings), `futuris/api/routers/models.py:30` (`mae_baseline: 42.0`), `market.py`/`universe_forecasting.py` evidence `content_hash="e3b0c442…"` (SHA-256 of ""), `snapshot_path` files that do not exist.
*Root cause:* early scaffolding returned placeholder values and was never replaced by persisted/measured data.
*Evidence:* payloads quoted in Phase 1; the UI explicitly refuses to display these numbers for exactly this reason.
*Fix:* serve persisted `EvaluationRunModel`/`OutcomeModel` data; return `404`/`data_quality:"insufficient_data"` instead of invented samples; label provenance (`live` vs `synthetic`) on every forecast and require it before the UI renders values; delete the fabricated `content_hash` (compute a real hash or omit evidence).

**C4 — Unauthenticated state-changing and expensive endpoints.**
*Files:* `futuris/api/routers/events.py:55,85` (webhook create/delete), `:36` (events), `futuris/api/routers/predictions.py:111,156,296` (predict / matrix / refresh-all),
`futuris/api/routers/market.py:361,382,401`, `futuris/api/routers/scenarios.py:28,55`,
`futuris/api/routers/ecosystem.py:57` (`/seed` uses `RequireViewer`), `futuris/api/routers/webhooks.py:149` (IntelX inbound).
*Root cause:* `futuris/infra/auth.py:get_current_user` returns an anonymous `public_viewer` `AuthUser` instead of 401; routes with no dependency therefore inherit anonymous access, and `RequireViewer` admits anonymous callers.
*Evidence:* probe table — anonymous `POST /v1/webhooks` → 201 (secret returned), `POST /v1/predictions/refresh-all` → 200, `POST /v1/ecosystem/seed` → 200.
*Fix:* make missing credentials 401 (keep a single explicit `AllowAnonymous` dependency for truly public reads); require analyst/admin on all mutation/trigger routes; validate webhook URLs against `PolicyEngine.evaluate_url` (already exists) to block SSRF/private ranges.

**C5 — Webhook subsystem is a silent no-op.**
*File:* `futuris/infra/events.py:96-119` (`break` on first attempt; `self._client is None` for the global emitter) and `:122`.
*Root cause:* retry loop breaks unconditionally; the global `event_emitter` is constructed without an `httpx.AsyncClient`; subscriptions live in memory only.
*Evidence:* 500-response probe made exactly 1 attempt; `event_emitter._client is None`; docs promise 3 attempts.
*Fix:* inject a shared async client in the app lifespan, retry on non-2xx/timeouts with backoff, count attempts with `WEBHOOK_DELIVERY_TOTAL`, persist subscriptions (or clearly document them as ephemeral and reject anonymous registration).

### HIGH

**H1 — Audit trail only exists on FRIDAY routes.** `SECURITY.md` §4 vs `futuris/api/routers/friday.py:402,690,740,826` being the only `AuditLogger` callers. Evidence: 0 audit rows after create/invalidate/resolve. *Fix:* log every mutation with actor + payload hash (central dependency or per-route), test it.

**H2 — `point_in_time_query` returns stale state.** `futuris/storage/repositories.py:222-249`. Only `forecast_created` events carry `payload["target"]`, so later status transitions are ignored: DB `invalidated` vs PIT `active`. *Fix:* persist `target` on every event (or reconstruct by replaying `forecast_updated` events); add a regression test.

**H3 — Duplicate outcome resolution → HTTP 500.** `OutcomeRepository.record_outcome` (`repositories.py:269`) vs `OutcomeModel.forecast_id UNIQUE`; `resolve-manual` doesn't pre-check. Evidence: `UNIQUE constraint failed: outcomes.forecast_id`. *Fix:* return 409 (or idempotent 200) and pre-check existence.

**H4 — Production secret guard never runs for the deployed config.** `futuris/upgrade/safe_config.py:30-39` returns early unless `env == "prod"`, but `render.yaml` sets `APP_ENV=production`, and the guard is additionally disabled unless `STRICT_PRODUCTION_SECRETS=true`; `validate_production_credentials` (`upgrade/auth.py`) is never called. Evidence: direct call with `APP_ENV="production"` + missing/placeholder secrets raised nothing. *Fix:* accept both spellings, fail closed when `APP_ENV` is production-like, wire the validator into startup, test both.

**H5 — The autonomous scheduler never starts.** `futuris/infra/scheduler.py:181-201` defines `start()`; nothing in `futuris/api/app.py` lifespan (or anywhere else in `futuris/`) calls it — only tests do. The advertised "continuous forecasting / nightly backtests / drift monitoring" do not run in the served product, and its jobs hardcode `SyntheticTelemetryConnector`. *Fix:* start/stop it in the lifespan (guarded by config), use real configured connectors, and add a boot test asserting jobs are scheduled.

**H6 — 4,905 committed `node_modules` files, and they are incomplete.** `git ls-files | grep -c node_modules` → 4905 (of 5142 tracked files); `futuris/ui/node_modules/typescript/lib` is absent → `npm run build` fails from a fresh clone (works after `npm ci`). `.gitignore` already lists `futuris/ui/node_modules/`. *Fix:* `git rm -r --cached futuris/ui/node_modules`, keep the ignore rule, document `npm ci`, and add the UI typecheck/build to CI.

**H7 — The project's own E2E verification script fails in a clean environment.** `scripts/e2e_system_test.py:257` builds headers from `settings.FUTURIS_API_KEY`, which is `None` without a `.env` → `TypeError: Header value must be str or bytes, not <class 'NoneType'>`; documented result is 13/13, actual 12/13. *Fix:* skip the API suite with an explicit "not configured" result (or generate a bootstrap key in-test), and stop claiming a pass count that requires unstated env vars.

**H8 — Documentation contradicts the repository (details in Phase 1 §3).** 82 vs 171 tests, "0 lint warnings" vs 478 errors, "13/13" vs 12/13, quickstart that corrupts the DB, webhook retry promise, security claim not implemented. *Fix:* make docs generated/honest, and make CI enforce lint instead of hiding it (`continue-on-error` currently guarantees the claim can never be true).

### MEDIUM

- **M1 — Guessable fallback credentials in code and sample config:** `connectors/trading_bot.py:31-32` (`trading_bot_default_key`, `read_key_default_secret_123`), `connectors/forge.py:26` (`forge_default_secret_key`), `connectors/nexus.py:27` (`nexus_default_token`), `.env.example` (`FUTURIS_API_KEY=futuris_api`, `FUTURIS_FRIDAY_API_KEY=friday_secret_key_default`). No real secret is committed, but **if any deployed environment used these defaults, rotate now**.
- **M2 — Quality gate manufactures inputs.** `upgrade/quality.py:62-77`: non-envelope objects get default prediction/lower/upper `0.0` and confidence 0.85/0.65/0.45, so `require()` can pass on fabricated numbers.
- **M3 — Fake provenance on universe/market forecasts:** `content_hash` = SHA-256 of empty string, `snapshot_path` may not exist (`core/universe_forecasting.py`, `api/routers/market.py`).
- **M4 — `GET /v1/forecasts` loads every row then paginates in Python** (`api/routers/forecasts.py:208-255`); no SQL `LIMIT/OFFSET`, so `limit` is not a resource bound.
- **M5 — `parse_horizon` silently coerces invalid input to 24h** (`api/routers/forecasts.py:36-47`) instead of returning 422.
- **M6 — UI reads the wrong error shape** (`futuris/ui/src/api/client.ts:22-33` expects `detail`/`message`; API returns `{"error":{"code","message"}}`), so users see raw JSON.
- **M7 — Lifecycle sweep swallows every exception and marks the forecast EXPIRED** (`core/lifecycle.py:150-160`), hiding genuine resolver bugs.
- **M8 — Wrong SDK example in `integrations/friday_client.py` docstring** (`ScenarioSpec.stress_spec(...)` does not exist).
- **M9 — `integrations/memora_client.py:10` hardcodes a Windows dev path into `sys.path`** (harmless fallback on Linux, dead/brittle code).
- **M10 — Rate limiting exists only for FRIDAY**; the anonymous heavy endpoints have no limits.
- **M11 — `futuris_demo.db` + `futuris_demo.db-journal` are committed** (163 KB + 95 KB, zero forecasts, unused) — remove from git.
- **M12 — Routers are mounted at multiple prefixes** (`/v1/friday` + `/api`; `/v1/futuris` + `/api/v1/futuris` + `/v1/market`; `/v1` + `/api/v1` for webhooks), duplicating the anonymous attack surface and the OpenAPI document.

### LOW

- **L1** 478 ruff findings (220 E501, 73 F401 unused imports, 61 T201 prints) — enforce a clean gate in CI.
- **L2** `upgrade/scheduler.py:43` truncates lease time with `now.replace(microsecond=0)` and uses `__import__("datetime").timedelta`.
- **L3** `upgrade/compat.py:_confidence_to_float` silently maps unknown values to `0.0`.
- **L4** `api/app.py:SPAStaticFiles.get_response` catches bare `Exception` (masks real static-file errors).
- **L5** `RequestIdMiddleware` echoes `X-Request-ID` but never logs it or returns it in error envelopes.
- **L6** CLI horizon parsing supports only `h`/`d` (no minutes) — inconsistent with the API's `m`.
- **L7** Startup demo seeding writes synthetic data without marking it as synthetic anywhere the API can expose (feeds C3's provenance work).
- **L8** Dead/unused imports and variables flagged by ruff (F401/F841).

---

## WHAT IS GENUINELY GOOD (do not regress)

- 171/171 tests pass; the forecasting pipeline, evidence snapshots, scenario engine, lifecycle, calibration maths and repositories work end-to-end (12/13 of the E2E harness passes on a clean checkout).
- `prediction_is_not_authorization` really is enforced — FRIDAY `execute`/`command` attempts get 403; `executable_commands` cannot be non-empty.
- FRIDAY auth fails closed (503 when unconfigured) and uses constant-time comparison.
- API keys are stored as SHA-256 hashes; `upgrade/auth.py` provides PBKDF2 credentials.
- Evidence snapshots are immutable (re-write raises), PII-denylisted, and hashed.
- The event repository is genuinely append-only.
- The UI's provenance discipline is the best expression of the product's intent and should be the template for the API fixes.
