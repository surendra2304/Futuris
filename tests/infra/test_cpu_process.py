from __future__ import annotations

import asyncio
import time

import pytest

from futuris.infra.cpu import run_cpu_process, shutdown_cpu_process_pool


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


@pytest.mark.asyncio
async def test_broken_pool_on_submit_is_discarded(monkeypatch: pytest.MonkeyPatch):
    import futuris.infra.cpu as cpu_module
    from concurrent.futures.process import BrokenProcessPool

    class AlreadyBrokenPool:
        shutdown_called = False

        def submit(self, _fn):
            raise BrokenProcessPool("worker exited while pool was idle")

        def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
            assert wait is False
            assert cancel_futures is True
            self.shutdown_called = True

    broken = AlreadyBrokenPool()
    monkeypatch.setattr(cpu_module, "_process_pool", broken)

    with pytest.raises(BrokenProcessPool, match="while pool was idle"):
        await cpu_module.run_cpu_process(
            "test", _add_numbers, 1, 2, _exclusive=False
        )

    assert cpu_module._process_pool is None
    assert broken.shutdown_called is True
