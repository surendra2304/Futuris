# Task 2 next-run instructions — Futuris

Last updated: 2026-10-20T23:13:19Z

Read .github/friday-continuation/task-2-checkpoint.md before coding. Resume branch automation/friday-universe-task-2; continue existing implementation rather than restarting repository review.

## Previous run evidence

- Result: failed
- Active coding seconds: 1729
- Coding passes: 4
- Source paths changed against main: 10

### Changed source paths

- futuris/core/pipeline.py
- futuris/core/schemas.py
- futuris/evaluation/calibration.py
- futuris/storage/repositories.py
- tests/core/test_provenance.py
- tests/evaluation/test_evaluation.py
- tests/storage/test_repositories.py
- tests/test_friday_delegation.py
- tests/test_prompt7_futuris.py
- uturis/api/routers/friday.py

### Runner failures or blockers

- Pass 4 failed with exit 1. See log pass-4.log.

## Detailed continuation notes from the previous coding pass

# Task 2 next-run instructions — Futuris

Read `.github/friday-continuation/task-2-checkpoint.md`, check branch/worktree, and continue from its exact first coding step; do not restart reconnaissance or discard existing work. Work only in Futuris. Do not commit, push, open PRs, edit workflows/infrastructure, or operate on the other assigned repositories.

The prior run added persistence coverage for explicit `nominal_coverage`, verified that current heuristic adapter bounds do not justify nominal labels, and removed a fabricated Brier score when no outcomes are resolved. Five focused suites passed (53 tests) and ruff plus `git diff --check` passed. The full suite and type checker remain unrun.

Exact first coding action: in `tests/core/test_provenance.py`, add a deterministic `CalibrationDecisionStage` regression using a `ModelPrediction` with no exceedance probability and generic asymmetric bounds; assert the pipeline does not invent `0.5` or percentile quantiles from those bounds. Then adjust `futuris/core/pipeline.py` to publish only values the model actually produces, preserving unlabeled generic bounds and FRIDAY's explicit-level-only nominal coverage. Update the FRIDAY API regression in `tests/test_prompt7_futuris.py` to match the corrected output contract.

Run:

```sh
.venv/bin/pytest tests/storage/test_repositories.py tests/test_friday_delegation.py tests/evaluation/test_evaluation.py tests/core/test_provenance.py tests/test_prompt7_futuris.py -o addopts= -q
.venv/bin/ruff check futuris/api/routers/friday.py futuris/core/schemas.py futuris/core/pipeline.py futuris/evaluation/calibration.py futuris/storage/repositories.py tests/test_friday_delegation.py tests/evaluation/test_evaluation.py tests/storage/test_repositories.py tests/core/test_provenance.py tests/test_prompt7_futuris.py
git diff --check
```

Current verification was **53 passed in 153.12s**; ruff and `git diff --check` passed. Preserve all existing worktree changes, keep generic interval bounds unlabeled, and update both handoff files before ending the next pass.


## First action next run

Read the checkpoint and these previous notes, inspect the branch diff, run the focused tests for the last implementation, fix failures, and implement the next highest-impact feature or regression fix. Record exact commands/results and revise both handoff files before ending.
