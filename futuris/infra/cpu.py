"""Bounded execution of CPU-bound work without blocking the event loop.

Forecast fitting is pure-Python/numpy work (statsforecast) that used to run
inline in coroutines. Measured on a 2-vCPU box: one request froze the event
loop for its whole 8.7s fit, so health checks, self-status and peer-agent calls
could not be answered while a forecast was being computed, and four concurrent
requests took 4x the wall time of one.

Everything that is CPU-bound goes through :func:`run_cpu`:

* work executes in a worker thread, so the loop keeps serving other requests;
* a per-loop semaphore sized to the CPU count bounds how many fits run at
  once, so a burst queues (and reports how long it queued) instead of thrashing
  the machine;
* queue wait and in-flight counts are exported as Prometheus metrics.
"""

from __future__ import annotations

import asyncio
import os
import time
import weakref
from collections.abc import Callable
from typing import Any, TypeVar

from futuris.infra.logging import get_logger
from futuris.infra.metrics import CPU_QUEUE_DEPTH, CPU_QUEUE_WAIT_SECONDS, CPU_WORK_IN_FLIGHT

logger = get_logger("futuris.infra.cpu")

T = TypeVar("T")

CPU_SLOTS = max(1, int(os.environ.get("FUTURIS_CPU_SLOTS") or (os.cpu_count() or 1)))

_gates: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


_pending = 0


def cpu_has_headroom() -> bool:
    """Whether one more request should run the full candidate search.

    Deliberately stricter than "a free slot exists": a CPU slot is kept for the
    cheap path so that a burst never queues *behind* a full search. Measured
    before this rule, a 6-request burst on 2 vCPUs took 54s; with two full
    searches admitted it took 18s; admitting one full search and answering the
    rest from the cheap path bounds the whole burst at ~9s.
    """
    return _pending < max(1, CPU_SLOTS - 1)


def cpu_saturated() -> bool:
    """True when new work should skip the expensive candidate fits."""
    return not cpu_has_headroom()


def cpu_queue_depth() -> int:
    """How many jobs are queued behind the busy slots (0 when idle)."""
    return max(0, _pending - CPU_SLOTS)


def cpu_gate() -> asyncio.Semaphore:
    """Per-loop semaphore bounding concurrent CPU-bound jobs to ``CPU_SLOTS``."""
    loop = asyncio.get_running_loop()
    gate = _gates.get(loop)
    if gate is None:
        gate = asyncio.Semaphore(CPU_SLOTS)
        _gates[loop] = gate
    return gate


async def run_cpu(
    kind: str, func: Callable[..., T], *args: Any, _exclusive: bool = True, **kwargs: Any
) -> T:
    """Run ``func`` in a worker thread.

    ``_exclusive`` jobs hold one of the CPU slots; degraded/cheap jobs such as a
    pressure-shedded candidate backtest pass ``_exclusive=False`` and run
    immediately instead of queueing behind a heavy fit.
    """
    global _pending
    _pending += 1
    CPU_QUEUE_DEPTH.set(cpu_queue_depth())
    try:
        if not _exclusive:
            return await _in_thread(kind, func, args, kwargs)
        gate = cpu_gate()
        if gate.locked():
            queued_at = time.perf_counter()
            async with gate:
                wait = time.perf_counter() - queued_at
                CPU_QUEUE_WAIT_SECONDS.labels(kind).observe(wait)
                logger.info("cpu_queue_wait", kind=kind, wait_seconds=round(wait, 3))
                return await _in_thread(kind, func, args, kwargs)
        async with gate:
            return await _in_thread(kind, func, args, kwargs)
    finally:
        _pending -= 1
        CPU_QUEUE_DEPTH.set(cpu_queue_depth())


async def _in_thread(
    kind: str, func: Callable[..., T], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> T:
    CPU_WORK_IN_FLIGHT.labels(kind).inc()
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    finally:
        CPU_WORK_IN_FLIGHT.labels(kind).dec()
