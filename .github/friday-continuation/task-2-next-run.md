# Task 2 next run — Futuris

1. Continue from PR #6, now updated to head commit `f579b4f5bcd7ed6b9de7e27bceebbf335331778b`.
2. Inspect latest CI/check results and logs for that exact SHA before changing code.
3. Run `pytest -q tests/test_backtest_leakage_regressions.py tests/test_prompt7_futuris.py::test_backtest_leakage_detection`.
4. Check naive and timezone-aware indexes, NaT index rejection, invalid timestamp columns, future timestamps, and malformed list timestamps.
5. Fix actual failures, push a focused commit, and update all three Task 2 handoff files with exact test/CI evidence. Do not merge automatically.