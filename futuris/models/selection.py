"""Candidate-model backtest selection shared by the pipeline and the engine.

Both forecast paths (``ForecastingPipeline`` and ``ForecastEngine``) used to own
a private copy of this loop. It is also where almost all of a forecast's CPU
time goes: on the configured telemetry series the two heavy candidates
(``mean_ensemble``, ``auto_ets``) cost ~4.5s each while every other candidate
costs milliseconds.

That makes this the right place to bound work under pressure:

* a wall-clock ``budget_seconds`` stops starting new fits once the request's
  deadline has passed -- which also keeps a cancelled/timed-out request from
  leaving a fit running in a worker thread;
* when every CPU slot is already taken, expensive candidates are skipped in
  favour of the cheap baselines.

Either way the decision is recorded (:class:`SelectionOutcome`) and carried into
``Forecast.model_metadata``, so a degraded selection is visible to the client
rather than silently changing the model behind a normal-looking forecast.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from futuris.infra.logging import get_logger
from futuris.infra.metrics import MODEL_SELECTION_DEGRADED_TOTAL
from futuris.models.base import ModelAdapter
from futuris.models.registry import model_registry

logger = get_logger("futuris.models.selection")

#: Candidates whose fit is cheap enough to always run (milliseconds).
CHEAP_CANDIDATES = frozenset({"naive", "drift", "seasonal_naive"})

#: Measured cost of the heavy candidates; used to refuse starting a fit that
#: cannot finish inside the caller's budget.
EXPENSIVE_FIT_ESTIMATE_SECONDS = 4.0


@dataclass
class SelectionOutcome:
    """Result of the held-out backtest, including what was *not* tried."""

    adapter: ModelAdapter
    score: float
    scores: dict[str, float] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    degraded: bool = False
    degraded_reason: str | None = None
    elapsed_seconds: float = 0.0
    is_fallback: bool = False

    def metadata(self) -> dict[str, object]:
        """Provenance fragment to merge into ``Forecast.model_metadata``."""
        meta: dict[str, object] = {
            "best_validation_score": round(self.score, 4),
            "candidate_scores": {k: round(v, 4) for k, v in sorted(self.scores.items())},
            "selection_degraded": self.degraded,
            "selection_elapsed_seconds": round(self.elapsed_seconds, 3),
        }
        if self.skipped:
            meta["candidates_skipped"] = sorted(self.skipped)
            meta["degraded_reason"] = self.degraded_reason
        if self.failed:
            meta["candidates_failed"] = sorted(self.failed)
        if self.is_fallback:
            meta["selection_fallback"] = "no_candidate_fit_succeeded"
        return meta


def select_best_adapter(
    candidates: list[str],
    *,
    x_train: pd.DataFrame,
    y_train: pd.Series,
    y_val: np.ndarray,
    as_of: datetime,
    val_steps: int,
    step_minutes: int,
    budget_seconds: float | None = None,
    cpu_saturated: bool = False,
) -> SelectionOutcome:
    """Backtest ``candidates`` and return the best adapter within the budget.

    Never returns ``None``: when every candidate fails, the naive baseline is
    used and the outcome says so.
    """
    started = time.monotonic()
    deadline = None if budget_seconds is None else started + max(0.0, budget_seconds)
    # When the request cannot afford the full search, evaluate the cheap
    # candidates first so a tight deadline still yields a backtested choice
    # instead of one arbitrary expensive fit.
    if budget_seconds is not None or cpu_saturated:
        candidates = [c for c in candidates if c in CHEAP_CANDIDATES] + [
            c for c in candidates if c not in CHEAP_CANDIDATES
        ]
    scores: dict[str, float] = {}
    failed: dict[str, str] = {}
    skipped: list[str] = []
    degraded_reason: str | None = None
    best_adapter: ModelAdapter | None = None
    best_score = float("inf")

    for name in candidates:
        expensive = name not in CHEAP_CANDIDATES
        if expensive and cpu_saturated:
            skipped.append(name)
            degraded_reason = degraded_reason or "cpu_pressure"
            continue
        if deadline is not None and scores:
            remaining = deadline - time.monotonic()
            needed = EXPENSIVE_FIT_ESTIMATE_SECONDS if expensive else 0.0
            if remaining <= needed:
                skipped.append(name)
                degraded_reason = degraded_reason or "model_budget_exhausted"
                continue

        adapter = model_registry.get_adapter(name)
        try:
            split_time = as_of - timedelta(minutes=val_steps * step_minutes)
            adapter.fit(x_train, y_train, as_of=split_time)
            val_pred = adapter.predict(val_steps)
            mae = float(np.mean(np.abs(np.array(val_pred.point_forecast) - y_val)))
            scores[name] = mae
            if mae < best_score:
                best_score = mae
                best_adapter = adapter
        except Exception as exc:
            failed[name] = str(exc)
            logger.warning("candidate_model_fit_failed", candidate=name, error=str(exc))
            continue

    is_fallback = False
    if best_adapter is None:
        is_fallback = True
        best_adapter = model_registry.get_adapter("naive")
        logger.warning(
            "all_candidate_models_failed_using_fallback",
            candidates=candidates,
            failures=failed,
        )

    if skipped:
        MODEL_SELECTION_DEGRADED_TOTAL.labels(reason=degraded_reason or "unknown").inc()
        logger.warning(
            "model_selection_degraded",
            reason=degraded_reason,
            skipped=skipped,
            fitted=sorted(scores),
        )

    return SelectionOutcome(
        adapter=best_adapter,
        score=best_score if best_score != float("inf") else 0.0,
        scores=scores,
        skipped=skipped,
        failed=failed,
        degraded=bool(skipped),
        degraded_reason=degraded_reason,
        elapsed_seconds=time.monotonic() - started,
        is_fallback=is_fallback,
    )
