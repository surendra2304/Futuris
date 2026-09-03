from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from .models import JobState


class InvalidTransition(RuntimeError):
    pass


_ALLOWED: dict[JobState, set[JobState]] = {
    JobState.CREATED: {JobState.QUEUED, JobState.CANCELLED},
    JobState.QUEUED: {JobState.RUNNING, JobState.CANCELLED},
    JobState.RUNNING: {JobState.PAUSED, JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED},
    JobState.PAUSED: {JobState.QUEUED, JobState.CANCELLED, JobState.FAILED},
    JobState.SUCCEEDED: set(),
    JobState.FAILED: {JobState.QUEUED},
    JobState.CANCELLED: set(),
}


@dataclass
class VersionedState:
    state: JobState
    version: int = 0
    updated_at: datetime = datetime.now(UTC)


class StateMachine:
    def __init__(self, state: JobState = JobState.CREATED) -> None:
        self._state = VersionedState(state=state, updated_at=datetime.now(UTC))
        self._lock = asyncio.Lock()

    async def transition(self, expected_version: int, target: JobState) -> VersionedState:
        async with self._lock:
            current = self._state
            if expected_version != current.version:
                raise InvalidTransition("stale version")
            if target not in _ALLOWED[current.state]:
                raise InvalidTransition(f"{current.state.value} -> {target.value} not allowed")
            self._state = VersionedState(target, current.version + 1, datetime.now(UTC))
            return self._state

    async def snapshot(self) -> VersionedState:
        async with self._lock:
            return VersionedState(self._state.state, self._state.version, self._state.updated_at)
