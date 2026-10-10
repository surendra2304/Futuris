# Phase 16 Notes — Live-Fire Hardening (2026-10-07/08)

The agent was driven the way its owner drives it — CLI, REST, FRIDAY delegation,
universe predictions, market forecasts, scenarios, webhooks, lifecycle sweeps —
against a real uvicorn with auth enforced, the scheduler and self-healing loop
running, and a seeded database. Then it was pushed to its dead ends with a new
harness (`scripts/extreme_pressure_harness.py`) plus the existing pressure,
adversarial, e2e and mesh harnesses. Everything below was found by execution,
not by reading.

## Environment
- Realistic runtime: generated 48-hex master + FRIDAY keys, `API_KEYS_ENABLED=true`,
  `SCHEDULER_ENABLED=true`, `SELF_HEALING_ENABLED=true`, startup demo seed on.
- Sandbox: 2 vCPU, Python 3.11.2, SQLite (WAL), node 22 for the UI build.
- Server bound to 0.0.0.0:8000, logs to `data/server.log`.

## Bugs found and fixed (each pinned by a regression test)

### B1 — Connection-pool exhaustion → 500s under concurrency (CRITICAL)
30 concurrent FRIDAY delegations: 15×201, **15×500** —
`QueuePool limit of size 5 overflow 10 reached, connection timed out`.
A request-scoped session holds its DB connection across the whole multi-second
pipeline run, so the default pool (5+10) exhausts fast.
Fix: `NullPool` for SQLite (every session its own cheap connection; WAL +
busy-timeout serialises writers), sized pool (10+20, timeout 15s, recycle,
pre-ping) for Postgres, and `sqlalchemy.exc.TimeoutError` mapped to a 503
`server_busy` envelope. Also: the unhandled-500 handler no longer returns
`str(exc)` to clients (internal detail leak).

### B2 — Idempotency race → duplicate forecasts (CRITICAL)
8 concurrent FRIDAY delegations with one `friday_request_id`: **8×201, 8
distinct forecasts**. Check-then-create with no unique constraint.
Fix: unique index `ix_forecasts_idempotency_key` (ORM + migration 0004 with
first-wins dedupe of pre-existing duplicates), `IntegrityError` → replay of the
winner in `delegate_forecast`, and same-key/different-payload → 409 (was:
silently served the wrong cached forecast).

### B3 — Lifecycle integrity: terminal states were overwritable (HIGH)
Invalidating a RESOLVED forecast → 200 (outcome row left claiming a resolution
the forecast no longer reports). Resolving an INVALIDATED forecast → 200.
Cancelling a resolved forecast → 200. Concurrent invalidate/resolve/cancel on
one forecast: all 200, last-writer-wins.
Fix: `ForecastRepository.transition_status` — a conditional
`UPDATE ... WHERE status IN (active, draft)`; the DB is the arbiter under races.
Invalidate/resolve/cancel now 409 on terminal states (both `/v1/forecasts/*`
and `/v1/friday/*` surfaces).

### B4 — Universe predictions served nonsense for non-capacity targets (HIGH)
`POST /v1/predictions/predict` for `forge:ci_cd:pipeline_failure_risk_24h`
(no caller context) returned `point_prediction=1285.0` (a demand number),
`probability=1.0`, range `[-5702, 10279]` — the capacity pipeline relabelled as
a CI failure rate. Unknown targets (`not:a:registered:target`) got a fabricated
prefix-deduced spec and a 200.
Fix: `DomainTargetSpec.pipeline_target` (true only for the checkout capacity
series); other targets without caller context return an honest
`insufficient_data` forecast naming the reason. `get_target_spec(strict=True)`
→ 422 for unregistered targets. Matrix picks latest per target across
active + insufficient_data + blocked (no more backfill churn on every read).

### B5 — Percent-scale risk classification compared 0–100 against 0–1 thresholds (MEDIUM)
Caller estimate 42.0 for a `%` target → `risk_level=CRITICAL` (42 ≥ 0.60), and
the interpretation rendered "0.0% probability" (probability None → 0.0).
Fix: `evaluate_risk_level` normalises percent-scale predictions; the
interpretation template gets the percent from the prediction when no
probability exists (`_display_probability_percent`).

### B6 — Negative interval lower bounds (MEDIUM)
Forecasts of non-negative series (demand, rates) carried lower bounds like
-5700 rpm (residual-std intervals around a small mean).
Fix: adapters clamp interval lower bounds at 0 when the training series is
non-negative (`_interval_floor`, ensemble included).

### B7 — The self-model lied about itself (MEDIUM)
`/v1/self/status` reported `scheduler: down` while the scheduler ran
(`_running_scheduler` was a lifespan *local*), and `agent_surface: down` on
current FastAPI (lazy `_IncludedRouter` has no `.path`, so the route walk saw
only 5 of 63 operations). `route_count` was wrong the same way.
Fix: module-level publish in the lifespan; duck-typed `iter_route_paths`
walker used by `check_agent_loop` and `get_capabilities`. Self-status now
reports `ok` with scheduler `running=True` and 60 routes.

### B8 — Request validation gaps (MEDIUM)
Empty target → **500** (quality-gate `ValueError` unhandled); 256-char target
accepted; non-numeric `context.point_estimate` silently ignored by
`/v1/forecasts`.
Fix: `min_length=1/max_length=255` + strip validator, shared
`futuris/api/validation.py` numeric-context validator on both routes,
gate rejection → 422 `quality_gate_rejected`.

### B9 — Self-status starved under load (MEDIUM)
Behind one forecast, `/v1/self/status` took 8–13s (the mesh's liveness probe;
the pressure harness failed its 5s stall threshold). Causes: 7 sequential
checks with ~15 DB queries, and `check_integrity` scanning *all* evidence rows.
Fix: `check_integrity` samples the latest 200 rows (reported as sampled);
`status()` runs under a 3s measurement budget — a check that cannot finish
reports `unknown` instead of stalling; assembled payload cached 2s (keeps its
`checked_at`). Verified live: probes 0.01–3.8s behind a running forecast.

### B10 — Seed trigger contended with the seed itself (LOW)
10 concurrent `POST /v1/ecosystem/seed`: 1×200 + 9×503 — the audit row for
*every* trigger (including rejected ones) took the write lock the running seed
needed.
Fix: audit only the accepted trigger; rejected `already_running` answers are
non-mutations and write nothing. Verified: 1 accepted + 9 already_running.

### B11 — Mesh harness was not re-runnable (LOW, harness bug)
`scripts/mesh_live_test.py` failed on re-run: FRIDAY idempotency (working as
designed) replayed the cached forecast, so no IntelX calls were observed.
Fix: wipe the mesh DB/WAL/storage at start. Verified 10/10 on a clean run.

### B12 — Committed placeholder credentials + default connector keys (MEDIUM, security)
`.env.example` shipped guessable keys (`futuris_api`, `friday_secret_key_default`,
`*_api`) and `API_KEYS_ENABLED=false` (contradicting the code default `true`).
Connectors silently sent public built-in defaults (`nexus_default_token`,
`forge_default_secret_key`, `intelx_default_token`, `trading_bot_default_key`,
`read_key_default_secret_123`). Master key compared with `==`.
`integrations/memora_client.py` inserted a hardcoded Windows path into sys.path.
Fix: `.env.example` ships empty values + `API_KEYS_ENABLED=true` + generation
instructions; `warn_if_default_credential` logs loudly (once per process) in
all four connectors; `hmac.compare_digest` for the master key; path guarded by
`is_dir()`. Also removed the committed empty `futuris_demo.db`(+journal) and
added `*.db*` to `.gitignore`.

### B13 — Audit trail was not universal (LOW)
SECURITY.md says every mutating action is audited; scenario runs, webhook
subscribe/delete and the seed trigger were not.
Fix: `AuditLogger` calls added (`run_scenarios`, `compare_scenarios`,
`create_webhook`, `delete_webhook`, `trigger_demo_seed`).

### B14 — `list_forecasts` loaded the whole table then sliced in Python (LOW, perf)
Fix: `list_filtered`/`count_filtered` with SQL `LIMIT/OFFSET`; `X-Total-Count`
is now the filtered count.

### B15 — UI served a stale claim and raw JSON errors (LOW)
`client.ts` parsed `detail`/`message` at the top level, but the API returns
`{"error": {...}}` → users saw raw JSON. `App.tsx` withheld all forecast
values claiming "Live provenance not exposed by API" — stale: the API labels
every forecast with `evidence_class`.
Fix: envelope parsing; the forecast workspace now renders values with
live/derived/synthetic/demo evidence pills and a verified-vs-synthetic count.
Rebuilt `dist/` (served live at `/ui/`).

### B16 — Dependency CVEs in the UI (LOW)
`npm audit`: 2 moderate in react-router-dom 6.x (CVE-2025-68470,
GHSA-337j-9hxr-rhxg) — used only by the unrouted pages.
Fix: upgraded to react-router-dom v7 (API-compatible for `Link`); build green;
`npm audit --omit=dev` → 0 vulnerabilities.

### B17 — Scheduler only ever read synthetic data (capability gap)
`ForecastScheduler` hardcoded `SyntheticTelemetryConnector`.
Fix: `FUTURIS_TELEMETRY_SOURCE=synthetic|nexus` + `NEXUS_URL`/`NEXUS_API_KEY`
settings + `connectors/factory.py`; scheduler pipeline and jobs use it.

### B18 — SDK docstring referenced a nonexistent method (LOW)
`friday_client.py` example used `ScenarioSpec.stress_spec(...)`; the real
builder is `ScenarioSpec.stress(demand_multiplier, capacity_multiplier)`.

## Verification (all executed)
- `scripts/extreme_pressure_harness.py` against the fixed live server: **12/12 PASS**
  (idempotent storm → 1 forecast; payload conflict → 409; concurrent resolution →
  1 outcome; lifecycle races → 409s; 30-way storm → 30×201; refresh storms → 200s;
  anonymous reads honest; rate limit 429 + honest 503 backpressure; webhook churn;
  seed single-flight; 19 dead-end cases; invalidate-resolved → 409).
- `tests/api/test_live_fire_regressions.py`: **22/22 PASS**.
- Full suite (335 tests), ruff (whole repo), pip-audit, e2e 13/13, adversarial
  1390 cases / 0 findings, mesh 10/10, pressure harness — see REPO_ANALYSIS.md
  verification log for the final post-fix run.
- Self-status probes behind a running forecast: 0.01–3.8s (was 8–13s).

## Known limits (honest, not bugs)
- SQLite single-writer: under a 110-way concurrent write burst, excess writers
  get 503 `storage_busy` (busy-timeout 30s) — honest backpressure; the
  production path is Postgres (asyncpg), sized pool configured.
- FRIDAY rate limit (100 req/h) and webhook subscriptions / idempotency store
  are in-memory per process; multi-worker deployments need Redis (documented
  in code).
- `GET /v1/market/forecast` is anonymous and compute-heavy by design (public
  dashboard); it refuses honestly (503) without live Stratex telemetry.

### B19 — Explicit heal served the liveness cache (MEDIUM, found by the full suite)
`POST /v1/self/heal` called `status()`, which since B9 serves a two-second cached
payload. A table dropped inside the window stayed missing while the heal returned
200 with the previous pass's actions. The cache was also process-wide, so a payload
measured against one test's database answered for the next test's database.
Fix: `status(force=True)` for the explicit heal and for the background healing loop
(operators and the loop must measure and heal, never replay); the cache entry now
carries the engine it was measured against and is only served for that engine.
Pinned by `test_explicit_heal_bypasses_the_status_cache`; the previously failing
`test_self_healing_recreates_a_dropped_table_while_serving` passes.

### B20 — `API_KEYS_ENABLED=false` granted every request admin (HIGH, security)
The development bypass in `futuris/infra/auth.py` returned `dev_admin` (role admin, scope `*`)
for every request whenever the flag was false, in any environment. The production guard only
checked the six secrets, so `APP_ENV=production` with the flag off booted and served everyone as
admin. Fix: `Settings.validate_production_safety` refuses the flag in production (import-time), and
`get_current_user` never honours the bypass in production (request-time). The dead `AUTH_DISABLED`
read was removed. Verified: `APP_ENV=production API_KEYS_ENABLED=false python -c "import futuris.infra.config"`
exits 1; dev still imports; tests `test_production_refuses_api_keys_disabled`,
`test_disabled_auth_is_never_honoured_in_production`, `test_disabled_auth_still_works_for_local_development`.

### B21 — Credentials read from the process environment ahead of Settings (MEDIUM)
Found by the coverage run: 11 FRIDAY and webhook tests failed with 401 when the shell had exported
`FUTURIS_FRIDAY_API_KEY` (the chain had sourced `/tmp/live-keys.env`). `verify_friday_auth` and the
inbound webhook guard called `os.getenv` before `Settings`, so a stray variable overrode the configured
key and the outcome depended on the caller's environment. Fix: both guards read `Settings` only; the
`FRIDAY_API_KEY` alias and `INTELX_WEBHOOK_API_KEY` are declared in `Settings` with `AliasChoices`;
five test sites moved from `setenv` to `monkeypatch.setattr(settings, ...)`. Pinned by
`test_friday_guard_follows_settings_not_the_process_environment`. Verified: the FRIDAY, auth,
hostile-input, governance and live-fire modules pass with the keys exported (108 passed).

### B22 — Alias collision inside Settings (LOW)
`FUTURIS_API_KEY` is the master-key field and also a validation alias of `MEMORA_API_KEY`. pydantic-settings
merges sources by key, so an exported `FUTURIS_API_KEY` overrode an explicit `Settings(FUTURIS_API_KEY=...)`.
No runtime effect in production (Settings is built once from the environment); the Memora alias is deliberate.
Fix: the Memora selection test isolates every variable in the alias chain. Recorded, not hidden.

### B23 — Request sessions bound to a stale storage factory (MEDIUM)
`futuris/api/deps.py` imported `async_session_factory` by name, so `get_db_session` always used the factory
that existed when the module was imported. Tests that rebind the storage module (the isolation fixtures)
had no effect on request sessions, and the universe tests intermittently received 503 `storage_busy`
because they wrote to the workspace database that a running server also held open. Fix: the dependency
resolves `storage_db.async_session_factory` at call time; the universe tests isolate storage through the
shared `ready_storage` fixture. Pinned by `test_request_sessions_follow_the_current_storage_factory`.

### Test-isolation pass (found by the final clean runs)
Two further isolation defects were found and fixed: an alias collision in the Memora selection test (B22)
and a probe test with an absolute latency bound that failed under coverage instrumentation (now a
relative bound, R14). Both changes keep the requirement; neither weakens a product assertion.
