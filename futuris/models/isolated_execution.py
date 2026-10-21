"""Process-isolated execution for CPU-heavy forecasting model fits.

StatsForecast's ETS optimiser can hold the CPython GIL for several seconds.
Moving the fit to a thread alone therefore does not guarantee that the async
server can answer lightweight health probes. This module confines candidate
backtests and the final refit/prediction to one long-lived spawned worker
process. Keeping a single process bounds memory use on small deployments.

If process execution is unavailable, the service degrades to cheap baseline
models rather than repeating the expensive work in a thread and starving the
event loop.
"""

from __future__ import annotations

import multiprocessing
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from futuris.infra.logging import get_logger
from futuris.infra.metrics import MODEL_SELECTION_DEGRADED_TOTAL
from futuris.models.base import ModelPrediction
from futuris.models.registry import model_registry
from futuris.models.selection import select_best_adapter

logger = get_logger("futuris.models.isolated_execution")


@dataclass
class IsolatedModelResult:
    """Pickle-safe value returned from a model worker."""

    prediction: ModelPrediction
    model_version: str
    best_score: float
    selection_metadata: dict[str, object]


_EXECUTOR: ProcessPoolExecutor | None = None
_EXECUTOR_LOCK = threading.Lock()


def _get_executor() -> ProcessPoolExecutor:
    """Create one persistent spawned worker, safe to call from worker threads."""
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is None:
            # Spawn avoids forking a running ASGI process with active threads.
            # A single worker deliberately bounds extra memory and native thread
            # pressure on low-resource Oracle Cloud deployments.
            _EXECUTOR = ProcessPoolExecutor(
                max_workers=1,
                mp_context=multiprocessing.get_context("spawn"),
            )
        return _EXECUTOR


def _reset_broken_executor(executor: ProcessPoolExecutor) -> None:
    """Forget a process pool after worker death so a later request may recover."""
    global _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is executor:
            _EXECUTOR = None
    executor.shutdown(wait=False, cancel_futures=True)


def _fit_predict_worker(
    candidates: list[str],
    x_train: pd.DataFrame,
    y_train: pd.Series,
    y_val: np.ndarray,
    x_full: pd.DataFrame,
    y_full: pd.Series,
    as_of: datetime,
    val_steps: int,
    step_minutes: int,
    horizon_steps: int,
    capacity_threshold: float | None,
    budget_seconds: float | None,
    cpu_saturated: bool,
) -> IsolatedModelResult:
    """Select, refit, and predict entirely inside the isolated process."""
    selection = select_best_adapter(
        candidates,
        x_train=x_train,
        y_train=y_train,
        y_val=y_val,
        as_of=as_of,
        val_steps=val_steps,
        step_minutes=step_minutes,
        budget_seconds=budget_seconds,
        cpu_saturated=cpu_saturated,
    )
    adapter = selection.adapter
    adapter.fit(x_full, y_full, as_of=as_of)
    prediction = adapter.predict(
        horizon=horizon_steps,
        capacity_threshold=capacity_threshold,
        probability_method="empirical",
    )

    version = model_registry.get_version_string(adapter)
    if selection.is_fallback:
        version = f"{version}:fallback_after_candidate_failures"

    return IsolatedModelResult(
        prediction=prediction,
        model_version=version,
        best_score=selection.score if np.isfinite(selection.score) else 0.0,
        selection_metadata=selection.metadata(),
    )


def fit_and_predict_isolated(
    *,
    candidates: list[str],
    x_train: pd.DataFrame,
    y_train: pd.Series,
    y_val: np.ndarray,
    x_full: pd.DataFrame,
    y_full: pd.Series,
    as_of: datetime,
    val_steps: int,
    step_minutes: int,
    horizon_steps: int,
    capacity_threshold: float | None,
    budget_seconds: float | None = None,
) -> IsolatedModelResult:
    """Run all costly model fits in a child process without blocking the event loop.

    The caller invokes this function from its existing bounded CPU worker
    thread. Waiting on the process future releases the GIL, allowing the ASGI
    event-loop thread to continue servicing light requests.

    A broken/unavailable worker falls back to cheap candidates inline. That
    fallback is intentionally explicit in the forecast metadata and avoids
    running AutoETS/AutoARIMA synchronously in the server process.
    """
    executor: ProcessPoolExecutor | None = None
    try:
        executor = _get_executor()
        result = executor.submit(
            _fit_predict_worker,
            candidates,
            x_train,
            y_train,
            y_val,
            x_full,
            y_full,
            as_of,
            val_steps,
            step_minutes,
            horizon_steps,
            capacity_threshold,
            budget_seconds,
            False,
        ).result()
    except Exception as exc:
        if isinstance(exc, BrokenProcessPool) and executor is not None:
            _reset_broken_executor(executor)
        logger.warning(
            "isolated_model_execution_failed_degrading_to_baselines",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        # Re-run only cheap baselines in the calling worker thread. This is a
        # bounded degradation path, never a fallback to another heavy fit.
        result = _fit_predict_worker(
            candidates,
            x_train,
            y_train,
            y_val,
            x_full,
            y_full,
            as_of,
            val_steps,
            step_minutes,
            horizon_steps,
            capacity_threshold,
            budget_seconds,
            True,
        )
        result.selection_metadata["model_fit_execution"] = "inline_degraded_fallback"
        result.selection_metadata["model_fit_worker_error"] = type(exc).__name__
        if result.selection_metadata.get("candidates_skipped"):
            result.selection_metadata["selection_degraded"] = True
            result.selection_metadata["degraded_reason"] = "model_worker_unavailable"
        else:
            # If routing selected only cheap candidates, the inline fallback
            # evaluated the same search space; record the execution fallback
            # without falsely claiming that model selection was degraded.
            result.selection_metadata["selection_degraded"] = False
            result.selection_metadata.pop("degraded_reason", None)
        # select_best_adapter already increments the metric in this process
        # when it skips candidates; do not count the degraded fallback twice.
        return result

    result.selection_metadata["model_fit_execution"] = "process"
    # Metrics updated in a child process are not visible to the API process.
    if result.selection_metadata.get("selection_degraded"):
        reason = str(result.selection_metadata.get("degraded_reason") or "unknown")
        MODEL_SELECTION_DEGRADED_TOTAL.labels(reason=reason).inc()
    return result
