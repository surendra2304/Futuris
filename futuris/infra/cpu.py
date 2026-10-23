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
import threading
import time
import traceback
import weakref
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from functools import partial
from multiprocessing import get_context
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


def _process_job_finished(kind: str, _future: Future[Any]) -> None:
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


class CPUWorkerError(RuntimeError):
    """An isolated CPU worker exited without returning a valid result."""

    def __init__(self, message: str, *, remote_traceback: str = "") -> None:
        super().__init__(message)
        self.remote_traceback = remote_traceback


def _isolated_process_worker(
    send_connection: Any,
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    """Execute one picklable CPU job in a disposable spawned child."""
    try:
        send_connection.send(("ok", func(*args, **kwargs)))
    except BaseException as exc:
        remote_traceback = traceback.format_exc()
        try:
            send_connection.send(("error", exc, remote_traceback))
        except BaseException:
            # Some custom exception instances cannot be pickled. Preserve the
            # useful type/message/traceback without risking a silent EOF.
            send_connection.send(
                (
                    "error_fallback",
                    type(exc).__module__,
                    type(exc).__name__,
                    str(exc),
                    remote_traceback,
                )
            )
    finally:
        send_connection.close()


def _terminate_and_join_process(process: Any, grace_seconds: float = 1.0) -> None:
    """Terminate a specific isolated worker and reap it before releasing a lease."""
    if process.pid is None:
        return
    if process.is_alive():
        process.terminate()
    process.join(timeout=grace_seconds)
    if process.is_alive():
        process.kill()
        process.join()


def _raise_worker_message(message: tuple[Any, ...]) -> Any:
    """Return worker output or re-raise a picklable remote exception."""
    status = message[0]
    if status == "ok":
        return message[1]
    if status == "error":
        exc, remote_traceback = message[1], message[2]
        if isinstance(exc, Exception):
            try:
                setattr(exc, "remote_traceback", remote_traceback)
            except Exception:
                pass
            raise exc
        raise CPUWorkerError(
            f"CPU worker raised {type(exc).__name__}: {exc}",
            remote_traceback=remote_traceback,
        )
    if status == "error_fallback":
        _, module, name, detail, remote_traceback = message
        raise CPUWorkerError(
            f"CPU worker raised {module}.{name}: {detail}",
            remote_traceback=remote_traceback,
        )
    raise CPUWorkerError("CPU worker returned an invalid result envelope")


async def run_cpu_process_cancellable(
    kind: str,
    func: Callable[..., T],
    *args: Any,
    _exclusive: bool = True,
    _timeout_seconds: float | None = None,
    **kwargs: Any,
) -> T:
    """Run CPU work in a disposable process that is killed on cancellation.

    Unlike ProcessPoolExecutor, a running function in this per-job process can
    be terminated when the coroutine is cancelled. This is essential for
    lease-protected heavy work: losing a distributed lease must not leave a
    stale model-fit process burning CPU after another worker acquires the lease.

    The function and all arguments/results must be picklable. Cancellation
    waits for the child to terminate and be reaped before returning to the
    coordinator, so a caller can safely release its lease afterwards.
    """
    global _process_pending
    if _timeout_seconds is not None and (
        isinstance(_timeout_seconds, bool)
        or not isinstance(_timeout_seconds, (int, float))
        or _timeout_seconds <= 0
    ):
        raise ValueError("_timeout_seconds must be positive or None")

    with _process_pending_lock:
        _process_pending += 1
    CPU_QUEUE_DEPTH.set(cpu_queue_depth())

    async def execute_isolated() -> T:
        context = get_context("spawn")
        receive_connection, send_connection = context.Pipe(duplex=False)
        process = context.Process(
            target=_isolated_process_worker,
            args=(send_connection, func, args, kwargs),
            name=f"futuris-{kind}-isolated-worker",
        )
        started = False
        started_at = time.monotonic()
        CPU_WORK_IN_FLIGHT.labels(kind).inc()
        try:
            process.start()
            started = True
            send_connection.close()
            while True:
                if receive_connection.poll():
                    try:
                        message = await asyncio.to_thread(receive_connection.recv)
                    except EOFError as exc:
                        await asyncio.to_thread(process.join)
                        raise CPUWorkerError(
                            f"CPU worker for {kind!r} exited without a result "
                            f"(exit code {process.exitcode})"
                        ) from exc
                    await asyncio.to_thread(process.join)
                    return _raise_worker_message(message)

                if not process.is_alive():
                    await asyncio.to_thread(process.join)
                    if receive_connection.poll():
                        message = await asyncio.to_thread(receive_connection.recv)
                        return _raise_worker_message(message)
                    raise CPUWorkerError(
                        f"CPU worker for {kind!r} exited without a result "
                        f"(exit code {process.exitcode})"
                    )

                if (
                    _timeout_seconds is not None
                    and time.monotonic() - started_at >= _timeout_seconds
                ):
                    await asyncio.to_thread(_terminate_and_join_process, process, 0.5)
                    raise TimeoutError(
                        f"isolated CPU worker {kind!r} exceeded "
                        f"{_timeout_seconds:g} seconds"
                    )
                await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            if started:
                # Do not release a coordinator lease until the old child has
                # actually stopped; cancellation of an await alone is not enough.
                await asyncio.to_thread(_terminate_and_join_process, process, 0.5)
            raise
        finally:
            receive_connection.close()
            send_connection.close()
            if started and process.is_alive():
                await asyncio.to_thread(_terminate_and_join_process, process, 0.5)
            CPU_WORK_IN_FLIGHT.labels(kind).dec()

    try:
        if not _exclusive:
            return await execute_isolated()

        gate = _process_gate()
        if gate.locked():
            queued_at = time.perf_counter()
            async with gate:
                wait = time.perf_counter() - queued_at
                CPU_QUEUE_WAIT_SECONDS.labels(kind).observe(wait)
                logger.info("cpu_process_queue_wait", kind=kind, wait_seconds=round(wait, 3))
                return await execute_isolated()
        async with gate:
            return await execute_isolated()
    finally:
        with _process_pending_lock:
            _process_pending = max(0, _process_pending - 1)
        CPU_QUEUE_DEPTH.set(cpu_queue_depth())


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
