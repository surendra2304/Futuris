# FUTURIS Master Engineering Plan

**Plan owner:** Repository maintainers  
**Repository:** `surendra2304/Futuris`  
**Baseline reviewed:** `main` at `2fc223ff102e65e0570af1b22a4df7c29f14a54c` (2026-10-21)  
**Plan created:** 2026-10-22  
**Status:** Active; Phase 0 is in implementation on a feature branch.

## Executive objective

Move FUTURIS from feature-rich forecasting service to a release process that is reproducible, evidence-led, concurrency-safe, and continuously verified. A phase is complete only when the implementation, regression tests, documentation, and CI evidence agree. Passing a single local run is not enough for concurrency-sensitive behavior.

FUTURIS produces advisory forecasts. Forecast output must never be represented as authorization to trade, execute a command, mutate infrastructure, or make an irreversible operational decision.

## Verified baseline and immediate blocker

The latest observed CI run on the baseline, [run 39660758525](https://github.com/surendra2304/Futuris/actions/runs/39660758525), completed with failure on 2026-10-21. Ruff passed. Pytest reported **486 passed, 2 failed, 1 xpassed** in about 16 minutes. The failures were:

- `tests/infra/test_scheduler_targets.py::test_scheduler_forecast_refresh_dispatches_friday_signal`: a test configured for one target observed three because dynamic subscription discovery added other trading targets.
- `tests/test_scheduler_and_pipeline.py::test_scheduler_jobs_direct_trigger`: a test scoped to one target observed four after its ingestion step dynamically expanded the subscription set.

The event-loop-liveness test was marked as an expected failure for defect B24 but passed on this current-head run (XPASS). An XPASS must be treated as a resolved regression, not left as a permanently waived test.

These findings establish the first repair: isolate unit tests from unrelated dynamic target discovery, assert the asynchronous FRIDAY dispatch really completed, remove the stale xfail marker, and make quality gates reject unexpected xpasses and coverage regressions. This does not claim the full suite is green until a current-head CI run confirms it.

## Engineering principles

1. **Evidence over claims.** Distinguish source inspection, deterministic unit tests, stress tests, and production telemetry. Record command, commit, environment, and result for each decisive check.
2. **No fabricated success.** Never substitute invented forecast values, provenance, calibration scores, peer state, persisted IDs, or deployment health for missing evidence.
3. **Correct contracts before more features.** Define concurrency, retry, failure, timeout, and persistence behavior before increasing pipeline complexity.
4. **Bound every background operation.** CPU work, peer calls, queues, retries, leases, and shutdown waits require explicit resource and time bounds.
5. **Test isolation is part of correctness.** Tests own their database, settings, clocks, rate-limit state, and external-service boundaries.
6. **Small reviewable pull requests.** One coherent concern per branch, all checks on the PR head, merge only with confirmed green required checks, then verify the resulting main commit.
7. **No placeholder implementations.** New code must have real behavior, defined errors, type-safe contracts, observability, and tests. Do not satisfy a metric with a fake implementation or loosen an assertion to hide a regression.

## Workstreams and acceptance criteria

### Phase 0 — Restore trustworthy green CI (in progress)

- Fix scheduler tests so tests for a single configured target are not coupled to live/dynamic pair discovery.
- Await the scheduled notification task and verify FRIDAY dispatch was awaited exactly once.
- Remove the obsolete expected-failure marker from the event-loop-liveness regression.
- Make unexpected xpasses fail the suite and enable strict marker handling.
- Enforce an 80% minimum Python coverage gate and give the CI job a finite timeout.
- **Exit criteria:** Ruff passes; the complete pytest suite passes without failures, errors, xpasses, or unreviewed skips; coverage meets the gate; Docker build succeeds on the same PR head.

### Phase 1 — Idempotency and transactional correctness

- Specify same-key semantics: an identical request replays the original result; a different payload for the same key returns a conflict; concurrent callers never create duplicate forecasts.
- Prove the contract using independent database sessions/connections, synchronization barriers, and repeated concurrent runs; cover both SQLite's single-writer behavior and PostgreSQL's intended production path.
- Keep uniqueness enforcement in the database. Handle constraint conflicts and transient lock contention without turning a known conflict into a 500 or returning a success before persistence is confirmed.
- End read transactions before long CPU-bound work where safe; keep write transactions short.
- **Exit criteria:** deterministic regression tests for 1, 6, and 30 concurrent same-key requests; same-key/different-payload tests; retry behavior documented; no intermittent duplicate creation or uncaught storage error in repeated stress runs.

### Phase 2 — Async execution, deadlines, and lifecycle

- Keep GIL-bound model fitting outside the ASGI event loop using a bounded, long-lived worker strategy; verify the event-loop test on CI rather than weakening the time bound.
- Ensure request cancellation, worker startup failure, worker crash/restart, overload, and shutdown each have explicit behavior.
- Supervise in-flight notification/outbox tasks, surface task failures, cap concurrency, and drain or cancel tasks during orderly shutdown.
- Propagate absolute deadlines and remaining budgets across pipeline stages and peer calls to avoid additive timeout blowups.
- **Exit criteria:** event loop remains responsive during fit, pressure shedding is observable, no unbounded task growth, and worker/resource tests cover failure/recovery paths.

### Phase 3 — Forecast quality and domain truth

- Reject malformed and unsupported model/provider outputs rather than manufacturing zeros, default confidence, or model identity.
- Keep synthetic, stale, partial, blocked, and live evidence distinct through request, model, persistence, readback, and UI.
- Validate target-specific units and risk scales; non-negative series must not produce impossible negative lower bounds.
- Calibration must be described as unmeasured when outcome evidence is absent; uncertainty intervals must retain their ordering and valid numeric bounds.
- **Exit criteria:** property/boundary tests for NaN/infinity, empty series, stale evidence, missing targets, interval validity, calibration sample counts, and serialization round-trips.

### Phase 4 — Storage, migrations, and lifecycle invariants

- Validate Alembic upgrade paths from an empty database and from prior supported revisions.
- Run persistence contract tests against SQLite and PostgreSQL, including foreign keys, unique keys, transaction rollback, flush ordering, and pagination.
- Keep forecast state transitions and outcome rows consistent. Failed writes must not be reported as persisted forecasts.
- Use a transactional outbox (where external delivery must be coupled to a database mutation) to avoid losing events between commit and notification.
- **Exit criteria:** migrations and repository contracts pass against both database engines; failure-injection tests prove rollback and no false success.

### Phase 5 — Security, API behavior, and auditability

- Fail closed for missing/weak production credentials; keep development fallbacks visibly non-production.
- Exercise authentication, authorization, request-size/shape limits, rate limits, CORS, webhook signatures, and error redaction with adversarial inputs.
- Audit every state-changing route and make audit failures' behavior explicit.
- Keep forecast APIs advisory-only, with no executable commands or implicit external mutation.
- **Exit criteria:** security regression suite passes; error payloads never expose SQL, secrets, tokens, or raw provider internals; API route inventory matches the documented access policy.

### Phase 6 — Ecosystem integrations and resilience

- Give each peer contract explicit deadlines, retry policy, response schema validation, circuit-breaker semantics, and bounded concurrency.
- Keep optional peers from blocking the core forecast path; preserve the distinction between unavailable evidence and measured evidence.
- Contract-test FRIDAY, Inference, IntelX, Memora, Stratex, Forge, Sentinel, VyralX, and other configured peers using deterministic fake servers plus selected live smoke checks.
- Ensure outbound signal notifications have observable outcomes and retry/idempotency behavior.
- **Exit criteria:** every integration has success, malformed response, timeout, circuit-open, recovery, and provenance tests.

### Phase 7 — Frontend contract and accessibility

- Verify the dashboard never paints unknown or missing evidence green by default and presents provenance, calibration status, stale evidence, and degraded forecasts accurately.
- Add reproducible UI test, type-check, and production-build gates; test API error envelopes and loading/empty/error states.
- Keep generated distribution assets synchronized with the source build.
- **Exit criteria:** UI tests, type-check, production build, and API contract tests pass, with no stale generated assets.

### Phase 8 — Supply chain and deployment hardening

- Use the lockfile for reproducible installs where the deployment supports it; review compatibility between `requirements.txt`, `pyproject.toml`, and `uv.lock`.
- Add dependency and container scans with pinned report artifacts and clear failure thresholds; distinguish production risk from development-tool advisories.
- Verify non-root execution, secret handling, health/readiness semantics, signal handling, migrations, and graceful shutdown in a built container.
- **Exit criteria:** reproducible dependency install, vulnerability scan reviewed, container build and smoke test pass, and deployment configuration is documented.

### Phase 9 — Architecture, dead code, performance, and release

- Build a module/import inventory; identify dead code only after checking dynamic imports, CLI entry points, migration use, plugins, and tests.
- Add a typed public contract and static checks where they catch defects rather than merely increasing tooling.
- Benchmark forecast latency, event-loop responsiveness, DB pool usage, memory growth, throughput, and queue depth at representative loads.
- Document supported deployment topology, operational thresholds, backup/restore, rollback, and on-call diagnostics.
- **Exit criteria:** each removed module has an evidence-backed safety check; performance baselines and release runbooks are committed; all required CI and container checks pass on the release commit.

## Pull-request and merge protocol

For each workstream:

1. Create a clean feature branch from the latest `main`; do not mix unrelated refactors into a fix.
2. Inspect the complete affected call path, schemas, configuration, tests, and failure behavior before editing.
3. Add a regression test that fails for the old defect and proves the intended contract.
4. Run focused tests and lint when a runtime checkout is available; use the PR's GitHub Actions run for authoritative CI when local execution is unavailable.
5. Review the patch for scope, migrations, secrets, backward compatibility, and whether any test was weakened.
6. Open a PR with summary, risk, test commands/results, and known limitations.
7. Merge only after all required checks for the current PR head are green and conflicts are resolved. Do not infer green status from an older SHA or a successful test subset.
8. After merge, inspect the merge commit and its CI run; confirm main is green before beginning the next dependent branch.

## Current phase record

- **Observed baseline:** `2fc223ff102e65e0570af1b22a4df7c29f14a54c`.
- **Observed CI:** run `39660758525`; lint success, pytest failure (486 passed / 2 failed / 1 xpassed), Docker step skipped because pytest failed.
- **Implemented in Phase 0 branch:** make scheduler tests deterministic with respect to dynamic discovery; ensure notification completion is asserted; remove the stale xfail; configure strict pytest marker/xfail handling; enforce minimum coverage in CI.
- **Pending evidence:** successful CI for the PR head, review of the final diff, merge, and post-merge main verification.
- **Not yet claimed:** whole-repository defect-free status, 100% coverage, PostgreSQL execution, production readiness, or a green main branch.
