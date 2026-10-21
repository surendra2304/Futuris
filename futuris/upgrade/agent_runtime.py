from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .cancellation import CancellationToken, await_or_cancel
from .models import JobState
from .state import StateMachine


@dataclass(frozen=True)
class AgentRunResult:
    state: JobState
    value: object | None
    attempts: int
    error: str | None = None


class AgentRuntime:
    """Bounded, cancellable agent loop suitable for Futuris advisory agents."""

    def __init__(self, max_iterations: int = 8) -> None:
        if isinstance(max_iterations, bool) or max_iterations < 1:
            raise ValueError("max_iterations must be a positive integer")
        self.max_iterations = max_iterations

    async def run(
        self,
        work: Callable[[int], Awaitable[object]],
        *,
        token: CancellationToken | None = None,
    ) -> AgentRunResult:
        token = token if token is not None else CancellationToken()
        machine = StateMachine(JobState.CREATED)
        current = await machine.snapshot()
        await machine.transition(current.version, JobState.QUEUED)
        current = await machine.snapshot()
        await machine.transition(current.version, JobState.RUNNING)

        attempts = 0
        try:
            for iteration in range(1, self.max_iterations + 1):
                token.throw_if_cancelled()
                attempts += 1
                result = await await_or_cancel(work(iteration), token)
                if result is not None:
                    snap = await machine.snapshot()
                    await machine.transition(snap.version, JobState.SUCCEEDED)
                    return AgentRunResult(JobState.SUCCEEDED, result, attempts)
            snap = await machine.snapshot()
            await machine.transition(snap.version, JobState.FAILED)
            return AgentRunResult(JobState.FAILED, None, attempts, "iteration limit reached")
        except asyncio.CancelledError:
            snap = await machine.snapshot()
            if snap.state == JobState.RUNNING:
                await machine.transition(snap.version, JobState.CANCELLED)
            raise
        except Exception as exc:
            snap = await machine.snapshot()
            if snap.state == JobState.RUNNING:
                await machine.transition(snap.version, JobState.FAILED)
            return AgentRunResult(JobState.FAILED, None, attempts, f"{type(exc).__name__}: {exc}")
