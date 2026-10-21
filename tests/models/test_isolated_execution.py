"""Regression tests for process-isolated forecasting model execution."""

from concurrent.futures import Future
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from futuris.models.isolated_execution import fit_and_predict_isolated


def _model_inputs() -> dict[str, object]:
    """Small, deterministic input set that exercises the real StatsForecast baseline."""
    index = pd.date_range("2026-08-28T00:00:00Z", periods=30, freq="5min")
    y_full = pd.Series(np.linspace(10.0, 39.0, num=30), index=index)
    x_full = pd.DataFrame({"driver": np.arange(30, dtype=float)}, index=index)
    return {
        "candidates": ["naive"],
        "x_train": x_full.iloc[:-3].copy(),
        "y_train": y_full.iloc[:-3].copy(),
        "y_val": y_full.iloc[-3:].to_numpy(),
        "x_full": x_full,
        "y_full": y_full,
        "as_of": datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
        "val_steps": 3,
        "step_minutes": 5,
        "horizon_steps": 3,
        "capacity_threshold": 1_000.0,
        "budget_seconds": 5.0,
    }


def test_model_fit_and_prediction_run_in_isolated_process() -> None:
    """A normal forecast executes in a process, not the ASGI server process."""
    result = fit_and_predict_isolated(**_model_inputs())

    assert result.selection_metadata["model_fit_execution"] == "process"
    assert result.model_version.startswith("naive@v1:")
    assert len(result.prediction.point_forecast) == 3
    assert all(np.isfinite(value) for value in result.prediction.point_forecast)
    assert np.isfinite(result.best_score)


@pytest.mark.parametrize("failure_stage", ["initialize", "submit"])
def test_unavailable_process_worker_degrades_to_cheap_baseline(
    monkeypatch, failure_stage: str
) -> None:
    """Worker failure must not trigger heavy fits in the server process."""

    class UnavailableExecutor:
        def submit(self, *args, **kwargs):
            future = Future()
            future.set_exception(RuntimeError("worker unavailable"))
            return future

    def unavailable_executor():
        if failure_stage == "initialize":
            raise RuntimeError("worker initialization failed")
        return UnavailableExecutor()

    monkeypatch.setattr(
        "futuris.models.isolated_execution._get_executor",
        unavailable_executor,
    )
    inputs = _model_inputs()
    inputs["candidates"] = ["auto_ets", "naive"]

    result = fit_and_predict_isolated(**inputs)

    assert result.selection_metadata["model_fit_execution"] == "inline_degraded_fallback"
    assert result.selection_metadata["model_fit_worker_error"] == "RuntimeError"
    assert result.selection_metadata["selection_degraded"] is True
    assert result.selection_metadata["degraded_reason"] == "model_worker_unavailable"
    assert "auto_ets" in result.selection_metadata["candidates_skipped"]
    assert result.model_version.startswith("naive@v1:")
    assert len(result.prediction.point_forecast) == 3
