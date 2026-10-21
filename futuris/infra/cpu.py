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
import threading
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from functools import partial
from multiprocessing import get_context
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


def _combined_pending() -> int:
    with _process_pending_lock:
        process_pending = _process_pending
    return _pending + process_pending


def cpu_has_headroom() -> bool:
    """Whether one more request should run the full candidate search.

    Keep one capacity slot for short/degraded work. Both thread and process jobs
    contribute to admission pressure, including process work still running after
    its original request was cancelled.
    """
    return _combined_pending() < max(1, CPU_SLOTS - 1)


def cpu_saturated() -> bool:
    """True when new work should skip expensive candidate fits."""
    return not cpu_has_headroom()


def cpu_queue_depth() -> int:
    """How many jobs exceed the configured CPU capacity (zero when idle)."""
    return max(0, _combined_pending() - CPU_SLOTS)


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


# The thread executor preserves responsiveness for I/O-heavy functions, but some
# numerical optimizers can hold the GIL for seconds. Default model fitting is
# therefore isolated in a small, persistent process pool. It is intentionally
# separate from run_cpu: only top-level, picklable worker functions belong here.
_process_pool_lock = threading.Lock()
_process_pending_lock = threading.Lock()
_process_pool: ProcessPoolExecutor | None = None
_process_pending = 0
_process_gates: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def _configured_process_workers() -> int:
    raw = os.environ.get("FUTURIS_CPU_PROCESS_WORKERS", "2")
    try:
        requested = int(raw)
    except ValueError as exc:
        raise RuntimeError("FUTURIS_CPU_PROCESS_WORKERS must be a positive integer") from exc
    if requested < 1:
        raise RuntimeError("FUTURIS_CPU_PROCESS_WORKERS must be a positive integer")
    return min(CPU_SLOTS, requested)


CPU_PROCESS_WORKERS = _configured_process_workers()


def _get_cpu_process_pool() -> ProcessPoolExecutor:
    """Lazily create the bounded spawn-based pool, safely across request threads."""
    global _process_pool
    with _process_pool_lock:
        if _process_pool is None:
            _process_pool = ProcessPoolExecutor(
                max_workers=CPU_PROCESS_WORKERS,
                mp_context=get_context("spawn"),
            )
        return _process_pool


def _process_gate() -> asyncio.Semaphore:
    """Return a semaphore bound to the current loop for process submissions."""
    loop = asyncio.get_running_loop()
    gate = _process_gates.get(loop)
    if gate is None:
        gate = asyncio.Semaphore(CPU_PROCESS_WORKERS)
        _process_gates[loop] = gate
    return gate


def _process_job_finished(kind: str, future: Future[Any]) -> None:
    """Update counters only when the submitted process work really finishes."""
    global _process_pending
    CPU_WORK_IN_FLIGHT.labels(kind).dec()
    with _process_pending_lock:
        _process_pending = max(0, _process_pending - 1)
    CPU_QUEUE_DEPTH.set(cpu_queue_depth())


def _discard_broken_pool(pool: ProcessPoolExecutor) -> None:
    """Allow a later request to construct a fresh pool after a worker crash."""
    global _process_pool
    with _process_pool_lock:
        if _process_pool is pool:
            _process_pool = None
    # Broken pools should be discarded without blocking the request loop.
    pool.shutdown(wait=False, cancel_futures=True)


async def run_cpu_process(
    kind: str,
    func: Callable[..., T],
    *args: Any,
    _exclusive: bool = True,
    **kwargs: Any,
) -> T:
    """Run a picklable CPU-bound operation in a bounded worker process.

    func must be a module-level function, and every argument/result must be
    picklable. A completion callback owns the process-pending counter so a
    cancelled HTTP waiter does not falsely make still-running CPU work look idle.
    """
    global _process_pending
    submitted = False
    with _process_pending_lock:
        _process_pending += 1
    CPU_QUEUE_DEPTH.set(cpu_queue_depth())

    async def submit_and_wait() -> T:
        nonlocal submitted
        pool = _get_cpu_process_pool()
        future = pool.submit(partial(func, *args, **kwargs))
        submitted = True
        CPU_WORK_IN_FLIGHT.labels(kind).inc()
        future.add_done_callback(lambda completed: _process_job_finished(kind, completed))
        try:
            return await asyncio.wrap_future(future)
        except BrokenProcessPool:
            _discard_broken_pool(pool)
            raise

    try:
        if not _exclusive:
            return await submit_and_wait()

        gate = _process_gate()
        if gate.locked():
            queued_at = time.perf_counter()
            async with gate:
                wait = time.perf_counter() - queued_at
                CPU_QUEUE_WAIT_SECONDS.labels(kind).observe(wait)
                logger.info("cpu_process_queue_wait", kind=kind, wait_seconds=round(wait, 3))
                return await submit_and_wait()
        async with gate:
            return await submit_and_wait()
    finally:
        # Once submitted, the worker-future callback decrements this counter
        # after actual completion (even if the HTTP task has been cancelled).
        if not submitted:
            with _process_pending_lock:
                _process_pending = max(0, _process_pending - 1)
            CPU_QUEUE_DEPTH.set(cpu_queue_depth())


def _shutdown_cpu_process_pool_sync() -> None:
    """Drain and close the pool without holding the application's event loop."""
    global _process_pool
    with _process_pool_lock:
        pool = _process_pool
        _process_pool = None
    if pool is not None:
        pool.shutdown(wait=True, cancel_futures=True)


async def shutdown_cpu_process_pool() -> None:
    """Gracefully drain and close model workers during application shutdown."""
    await asyncio.to_thread(_shutdown_cpu_process_pool_sync)
