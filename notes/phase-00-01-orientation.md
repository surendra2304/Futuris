# Phase 0/1 Notes — Ground Truth & Stack

## Repo identity
- Repo: `surendra2304/Futuris` @ `/home/user/Futuris`, branch `arena/9dca2f7e-futuris`, HEAD `a9ddb90` (squash-merge of PR #1 from `arena/01a10cc5-futuris`), single commit in local git history (history squashed; real history lives in diary/*.md 2026-08-28 → 2026-09-12).
- Project: FUTURIS v2.0.0 — "Operational Capacity & Predictive Intelligence Platform": calibrated probabilistic forecasting, evidence provenance, scenario analysis, advisory decision support. Part of a 9-agent "FRIDAY Universe" ecosystem (Memora, Stratex, IntelX, Sentinel, Cortex, Forge, NEXUS, Inference).
- Core invariant (README/SECURITY.md): **Prediction ≠ Authorization** — forecasts never execute mitigations.

## Size
- `futuris/` package: 121 .py files, 18,255 LOC
- `tests/`: 79 .py files, 9,291 LOC (incl. ~40 test modules)
- `scripts/`: 1,710 LOC (harnesses: e2e_system_test, adversarial_harness, pressure_harness, mesh_live_test…)
- UI: `futuris/ui/src` ~2,043 LOC (React+Vite+TS+Tailwind); built `dist/` committed (index-DiqNAz4q.js, index-eZ_yEUY8.css)
- Total tracked files: 269; node_modules NOT committed (prior audit's H6 claim is stale for this checkout)
- `futuris_demo.db` (163KB) + `futuris_demo.db-journal` (95KB) ARE committed — binary SQLite artifacts in git (prior audit M11; still true)

## Meta files read
- README.md (quickstart: docker compose → alembic upgrade head → cli demo → cli serve; architecture map; "Phases 5–6 queued" note is stale — NEXUS/FRIDAY adapters exist)
- SECURITY.md (provenance guarantees, prediction≠authorization, model promotion gate, RBAC roles, append-only audit)
- SYSTEM_MANIFEST.md (Render deployment URL https://futuris-th6f.onrender.com, env var contract)
- AUDIT_REPORT.md (2026-09-01 self-audit by "Antigravity": claims 82 tests pass, 0 lint warnings, 5 bugs fixed, 3 security fixes)
- FUTURIS_DIARY.md + diary/2026-08-28…09-12.md (9-day build log; claims 82→171 tests, "100% green, 0 lint warnings")
- docs/events.md (webhook HMAC-SHA256 contract, event schemas), docs/audit/PHASE-1-2-AUDIT.md (2026-10-05 prior Arena agent audit — see below)
- scripts/runbook.md (5-min quickstart + admin key creation)
- .github/workflows/ci.yml: lint job (ruff on memora_event_consumer only, then `ruff check .` with **continue-on-error: true**), test job (pytest)
- Dockerfile: multi-stage python:3.11-slim, tini entrypoint, healthcheck on /health, runs `python -m futuris.cli serve`
- docker-compose.yml: postgres:16-alpine + api; **hardcoded postgres/postgres credentials**
- render.yaml: Render free plan, singapore, `API_KEYS_ENABLED=true`, `APP_ENV=production`, sqlite at /app/data/futuris.db, secrets via `sync: false`

## Prior audit (docs/audit/PHASE-1-2-AUDIT.md, 2026-10-05, branch arena/01a10cc5-futuris @ a86c3f4)
Found 5 CRITICAL (C1 /v1/models 500 dead route; C2 alembic migration corrupts SQLite; C3 fabricated/hardcoded data in evaluation/market/universe routers; C4 unauthenticated mutating endpoints; C5 webhook subsystem no-op), 8 HIGH, 12 MEDIUM, 8 LOW.
**Current HEAD appears to incorporate fixes**: app.py lifespan now injects httpx client into event_emitter and starts ForecastScheduler; auth.py has ANONYMOUS_VIEWER + ROLE_HIERARCHY with require_viewer rejecting anonymous; config.py `API_KEYS_ENABLED` default now True; db.py has ensure_schema + add_missing_columns self-healing; deps.py session teardown hardened; errors.py maps storage errors to 503 + sanitises 422 details; repositories.py point_in_time_query now replays lifecycle events; EventRepository append-only enforced.
=> MUST re-verify each prior finding against current tree rather than trust either doc.

## Stack (pyproject.toml + requirements.txt, identical lists)
Python >=3.11; build hatchling; deps: fastapi>=0.110, uvicorn[standard]>=0.28, pydantic>=2.6, pydantic-settings>=2.2, sqlalchemy[asyncio]>=2.0.28, asyncpg>=0.29, alembic>=1.13.1, apscheduler>=3.10.4, structlog>=24.1, httpx>=0.27, statsforecast>=1.7, scikit-learn>=1.4, pandas>=2.2, numpy>=1.26, aiosqlite>=0.20, python-dotenv>=1.0, prometheus-client>=0.20, typer>=0.12, rich>=13.7. dev: pytest, pytest-asyncio, ruff.
Ruff config: line-length 100, target py311, select E,F,I,N,W,UP,B,A,C4,T20,RET,SIM,ARG; ignore B008; per-file ignores for tests/scripts/alembic.
pytest: asyncio_mode=auto, testpaths=tests, pythonpath=[".]
uv.lock present (456KB) — uv used by author; requirements.txt is the pip path.

## Immediate red flags spotted (verify in Phase 10)
- `.env.example` ships default keys: `FUTURIS_API_KEY=futuris_api`, `FUTURIS_FRIDAY_API_KEY=friday_secret_key_default`, `*_API_KEY=inference_api|memora_api|stratex_api|intelx_api`
- `docker-compose.yml` hardcodes `POSTGRES_PASSWORD: postgres`
- connectors reportedly have fallback default keys (prior audit M1: trading_bot.py:31-32, forge.py:26, nexus.py:27)
- `futuris_demo.db` committed; `.dockerignore` excludes `*.db` but `.gitignore` does NOT ignore `*.db`
- config.py reads `getattr(settings, "AUTH_DISABLED", False)` — not a declared field, extra="ignore" => always False (dead code)
- Master key compared with `==` (not constant-time) in infra/auth.py
- CI lint step is `continue-on-error: true` — lint gate is decorative
- `MEMORA_API_KEY` has AliasChoices including `FUTURIS_API_KEY` — master key doubles as Memora key
