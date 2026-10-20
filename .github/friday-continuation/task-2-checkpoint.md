# Task 2 checkpoint — Futuris

Updated: 2026-10-20T23:12:50Z

## Workstream mandate

Task 2 covers quantitative strategy, evidence-driven research, and predictive intelligence. This checkout is Futuris; work only here. Implement reproducible, time-aware evaluation, preserve honest uncertainty/provenance, and keep predictive decision support separate from automatic high-impact actions. Do not commit, push, open PRs, edit workflows, or operate on the other assigned repositories. Never perform live trading or expose credentials.

## Repository state

- Branch: `automation/friday-universe-task-2`
- HEAD: `d2871f1 fix(automation): preserve coding logs for task 2`
- Worktree contains earlier-pass changes plus this pass. This pass changed:
  - `futuris/api/routers/friday.py`
  - `futuris/core/schemas.py`
  - `futuris/core/pipeline.py`
  - `tests/test_friday_delegation.py`
  - `tests/storage/test_repositories.py`
  - `tests/core/test_provenance.py`
  - `tests/test_prompt7_futuris.py`
- Carried forward from earlier passes:
  - `futuris/evaluation/calibration.py`
  - `futuris/storage/repositories.py`
  - `tests/evaluation/test_evaluation.py`
- `.github/friday-continuation/` handoff files are untracked worker state and must remain maintained.

## Implemented

1. FRIDAY calibration retains `range_coverage` for generic `range_lower`/`range_upper` bounds, and adds `nominal_interval_coverage`, keyed by declared nominal level (for example `"0.9"`), with empirical coverage and sample count.
2. Nominal coverage is measured only for finite interval `lower`/`upper` bounds paired with a finite observed value and an explicit `nominal_coverage` strictly between 0 and 1. Missing labels, invalid levels, reversed/non-finite bounds, and unusable observations are excluded; levels with multiple interval entries in one forecast are excluded because a single forecast-level outcome cannot be unambiguously paired with multiple same-level step intervals.
3. Changed the FRIDAY response's generic bounds fallback from a misleading `"90%"` label to an unlabeled `{lower, upper}` entry. Forecast bounds are not considered nominal unless the forecast explicitly records that declaration.
4. Documented the explicit `nominal_coverage` requirement in the `Forecast.intervals` schema description. The pipeline/model adapters currently produce unlabeled interval bounds; no nominal level was inferred or added.
5. Added tests for declared 80% and 90% coverage, generic unlabeled intervals, invalid nominal level, ambiguous duplicate levels, and the unlabeled response fallback.
6. Earlier passes also fixed resolved-pair calibration, target-specific ECE, chronological trend behavior, ECE parity, and deterministic ordering of equal-time outcome repository results; see the existing tests and modified files.
7. Added a `ForecastRepository.create()`/`get()` regression proving explicit per-step `nominal_coverage` metadata round-trips unchanged.
8. Inspected interval production and propagation: adapters use `1.645 * in-sample residual standard deviation * sqrt(step)` for bounds; residuals are simple lagged differences, and the ensemble takes a component-bound envelope. This does not establish calibrated 90% coverage, so adapter and pipeline bounds remain unlabeled; the pipeline serializes no nominal level.
9. Removed the pipeline's `brier_score = p * (1-p)` value when no resolved outcomes exist. That quantity is not a measured Brier score; forecasts now report only `n_resolved_outcomes: 0` and `calibration_status: "uncalibrated"`. Added unit/API regressions for absence of the score and for unlabeled generated bounds.

## Verification

- `.venv/bin/pytest tests/storage/test_repositories.py tests/test_friday_delegation.py tests/evaluation/test_evaluation.py tests/core/test_provenance.py tests/test_prompt7_futuris.py -o addopts= -q` — **53 passed in 153.12s**.
- `.venv/bin/ruff check futuris/api/routers/friday.py futuris/core/schemas.py futuris/core/pipeline.py futuris/evaluation/calibration.py futuris/storage/repositories.py tests/test_friday_delegation.py tests/evaluation/test_evaluation.py tests/storage/test_repositories.py tests/core/test_provenance.py tests/test_prompt7_futuris.py && git diff --check` — **passed**.
- No standalone type-check command is configured in `pyproject.toml`. The full repository suite was not run. An earlier backtest test warned that optional IntelX at `http://intelx:8000` was unavailable; it completed deterministically without remote evidence.

## Unresolved work

- Existing adapter intervals are heuristic, based on simple residual estimates rather than a defensible calibrated interval procedure. They remain intentionally unlabeled; future nominal coverage requires out-of-sample validation and explicit provenance.
- Multiple same-level intervals within one forecast are currently omitted from level-specific coverage rather than miscounted as independent samples. If a future outcome schema supports step-aligned observations, revisit this policy with tests.
- Pipeline predictive-distribution p10/p25/p50/p75/p90 values are currently interpolated from aggregate bounds and a central estimate, and the distribution's exceedance probability defaults to 0.5 when the adapter has none. These values may overstate what was actually estimated; inspect and correct the output contract next.
- Trend minimum sample size (10) and threshold (0.01) remain fixed policy values, not configuration.
- No full repository suite or standalone type checker was run.
- Older clients may expect fabricated target/trend values or `coverage_90_pct`; no in-repository dependency was found in earlier passes.

## Exact first coding step next run

In `tests/core/test_provenance.py`, add a deterministic `CalibrationDecisionStage` regression using a `ModelPrediction` with no exceedance probability and generic asymmetric bounds; assert the pipeline does not invent `0.5` or percentile quantiles from those bounds. Then adjust `futuris/core/pipeline.py` to publish only values the model actually produces, preserving unlabeled generic bounds and explicit-level-only coverage. Update the FRIDAY API regression in `tests/test_prompt7_futuris.py` for the resulting output contract. Run those tests plus the FRIDAY, evaluation, and repository suites, ruff on changed Python files, and `git diff --check`.


## Scheduled run summary (2026-10-20T23:13:19Z)

- Result: failed
- Active coding seconds: 1729
- Coding passes: 4
- Changed source paths: 10
- Failures: Pass 4 failed with exit 1. See log pass-4.log.
