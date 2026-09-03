from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Awaitable, Callable


@dataclass(frozen=True)
class ScheduleSpec:
    job_id: str
    interval_seconds: int
    timezone: str = "UTC"
    enabled: bool = True


@dataclass
class JobLease:
    job_id: str
    owner: str
    acquired_at: datetime
    expires_at: datetime


class DistributedLeaseTable:
    """In-memory contract used in tests; production implementation should use DB/Redis SETNX."""

    def __init__(self) -> None:
        self._leases: dict[str, JobLease] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, job_id: str, owner: str, lease_seconds: int = 120) -> bool:
        now = datetime.now(UTC)
        async with self._lock:
            current = self._leases.get(job_id)
            if current and current.expires_at > now and current.owner != owner:
                return False
            self._leases[job_id] = JobLease(
                job_id, owner, now, now.replace(microsecond=0) + __import__("datetime").timedelta(seconds=lease_seconds)
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
    """Schedule definition plus single-flight execution contract."""

    def __init__(self, lease_table: DistributedLeaseTable | None = None) -> None:
        self.leases = lease_table or DistributedLeaseTable()
        self._tasks: set[asyncio.Task] = set()

    async def run_once(
        self, spec: ScheduleSpec, owner: str, fn: Callable[[], Awaitable[object]]
    ) -> object | None:
        acquired = await self.leases.acquire(spec.job_id, owner)
        if not acquired:
            return None
        try:
            return await fn()
        finally:
            await self.leases.release(spec.job_id, owner)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
