# Task 2 checkpoint — Futuris

Updated: 2026-10-22 UTC

## Implementation
- Durable/PR branch: `automation/friday-universe-task-2`.
- Focused branch: `fix/task-2-regressions-2026-10-22`, based on main `2fc223ff102e65e0570af1b22a4df7c29f14a54c`.
- Focused commit: `2f4ad80e676fee56bf51857c32853cb79cd61ee6`.
- Applied to existing PR branch in commit: `f579b4f5bcd7ed6b9de7e27bceebbf335331778b`.
- Files: `futuris/evaluation/backtest.py`; added `tests/test_backtest_leakage_regressions.py`.
- Fix: normalize naive/aware DataFrame indexes to UTC, reject NaT and malformed non-null timestamps, and ensure temporal leakage errors include actual values.

## Validation
- Local tests NOT RUN; laptop test connector returned HTTP 404.
- Existing PR #6 was updated rather than opening a duplicate: https://github.com/surendra2304/Futuris/pull/6
- CI status must be checked on the new head `f579b4f5bcd7ed6b9de7e27bceebbf335331778b`; no pass is claimed yet.
- Active duration was not reliably measured; the 30-minute target is not claimed.

## Next action
Inspect PR #6 checks and logs for the new head. Run `pytest -q tests/test_backtest_leakage_regressions.py tests/test_prompt7_futuris.py::test_backtest_leakage_detection`, fix failures, and update this handoff with exact results.