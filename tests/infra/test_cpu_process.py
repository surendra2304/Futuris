from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from futuris.infra.cpu import (
    run_cpu_process,
    run_cpu_process_cancellable,
    shutdown_cpu_process_pool,
)


def _add_numbers(left: int, right: int) -> int:
    return left + right


def _cpu_loop(duration_seconds: float) -> int:
    """Busy-spin in the child process without yielding the GIL in the parent."""
    deadline = time.monotonic() + duration_seconds
    iterations = 0
    while time.monotonic() < deadline:
        iterations += 1
    return iterations


def _raise_worker_error() -> None:
    raise ValueError("worker error")



def _write_pid_then_wait(marker_path: str, duration_seconds: float) -> int:
    """Record the actual child PID before blocking long enough to be cancelled."""
    Path(marker_path).write_text(str(os.getpid()), encoding="utf-8")
    time.sleep(duration_seconds)
    return os.getpid()


@pytest.mark.asyncio
async def test_cancellable_worker_terminates_the_actual_process(tmp_path: Path):
    marker = tmp_path / "child.pid"
    task = asyncio.create_task(
        run_cpu_process_cancellable(
            "cancel-test",
            _write_pid_then_wait,
            str(marker),
            30.0,
            _exclusive=True,
        )
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert marker.exists(), "child process did not start"
        child_pid = int(marker.read_text(encoding="utf-8"))

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)

        # Joining the child must reap it before the coroutine releases its lease.
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancellable_worker_timeout_terminates_child(tmp_path: Path):
    marker = tmp_path / "timeout-child.pid"
    with pytest.raises(TimeoutError, match="exceeded"):
        await run_cpu_process_cancellable(
            "timeout-test",
            _write_pid_then_wait,
            str(marker),
            30.0,
            _exclusive=True,
            _timeout_seconds=5.0,
        )
    assert marker.exists()
    child_pid = int(marker.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


@pytest.mark.asyncio
async def test_cancellable_worker_preserves_remote_errors():
    with pytest.raises(ValueError, match="worker error"):
        await run_cpu_process_cancellable(
            "error-test",
            _raise_worker_error,
            _exclusive=True,
        )


@pytest.mark.asyncio
async def test_process_worker_runs_picklable_work_and_reuses_pool():
    try:
        assert await run_cpu_process("test", _add_numbers, 19, 23, _exclusive=False) == 42
        assert await run_cpu_process("test", _add_numbers, 1, 2, _exclusive=False) == 3
    finally:
        await shutdown_cpu_process_pool()


@pytest.mark.asyncio
async def test_process_worker_error_propagates_without_corrupting_pool():
    try:
        with pytest.raises(ValueError, match="worker error"):
            await run_cpu_process("test", _raise_worker_error, _exclusive=False)
        assert await run_cpu_process("test", _add_numbers, 2, 3, _exclusive=False) == 5
    finally:
        await shutdown_cpu_process_pool()


@pytest.mark.asyncio
async def test_process_computation_does_not_starve_event_loop():
    ticks = 0
    finished = asyncio.Event()

    async def heartbeat() -> None:
        nonlocal ticks
        while not finished.is_set():
            ticks += 1
            await asyncio.sleep(0.01)

    pulse = asyncio.create_task(heartbeat())
    try:
        work = asyncio.create_task(
            run_cpu_process("test", _cpu_loop, 0.35, _exclusive=False)
        )
        result = await asyncio.wait_for(work, timeout=10)
        assert result > 0
        assert ticks >= 5
    finally:
        finished.set()
        await pulse
        await shutdown_cpu_process_pool()
