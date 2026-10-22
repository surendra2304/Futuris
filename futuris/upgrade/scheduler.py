from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class ScheduleSpec:
    """Validated contract for a scheduled job."""

    job_id: str
    interval_seconds: int
    timezone: str = "UTC"
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.job_id.strip():
            raise ValueError("job_id must not be empty")
        if (
            isinstance(self.interval_seconds, bool)
            or not isinstance(self.interval_seconds, int)
            or self.interval_seconds <= 0
        ):
            raise ValueError("interval_seconds must be a positive integer")
        if not self.timezone.strip():
            raise ValueError("timezone must not be empty")


@dataclass
class JobLease:
    job_id: str
    owner: str
    acquired_at: datetime
    expires_at: datetime


class DistributedLeaseTable:
    """In-memory lease implementation for tests; production needs DB/Redis atomic leases."""

    def __init__(self) -> None:
        self._leases: dict[str, JobLease] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, job_id: str, owner: str, lease_seconds: int = 120) -> bool:
        if not job_id.strip():
            raise ValueError("job_id must not be empty")
        if not owner.strip():
            raise ValueError("owner must not be empty")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or lease_seconds <= 0
        ):
            raise ValueError("lease_seconds must be a positive integer")

        now = datetime.now(UTC)
        async with self._lock:
            current = self._leases.get(job_id)
            if current and current.expires_at > now:
                return False
            self._leases[job_id] = JobLease(
                job_id=job_id,
                owner=owner,
                acquired_at=now,
                expires_at=now + timedelta(seconds=lease_seconds),
            )
            return True

    async def release(self, job_id: str, owner: str) -> bool:
        async with self._lock:
            current = self._leases.get(job_id)
            if not current or current.owner != owner:
                return False
            del self._leases[job_id]
            return True


class SafeScheduler:
    """Schedule definition plus single-flight execution and task lifecycle management."""

    def __init__(self, lease_table: DistributedLeaseTable | None = None) -> None:
        self.leases = lease_table or DistributedLeaseTable()
        self._tasks: set[asyncio.Task[object]] = set()

    async def run_once(
        self, spec: ScheduleSpec, owner: str, fn: Callable[[], Awaitable[object]]
    ) -> object | None:
        if not spec.enabled:
            return None
        acquired = await self.leases.acquire(spec.job_id, owner)
        if not acquired:
            return None
        try:
            task = asyncio.create_task(fn(), name=f"futuris-schedule:{spec.job_id}")
        except BaseException:
            await self.leases.release(spec.job_id, owner)
            raise
        self._tasks.add(task)
        try:
            return await task
        finally:
            self._tasks.discard(task)
            await self.leases.release(spec.job_id, owner)

    async def shutdown(self) -> None:
        """Cancel and drain active job functions before the scheduler is discarded."""
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.difference_update(tasks)
