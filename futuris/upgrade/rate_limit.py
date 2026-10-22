from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: float


class InMemoryRateLimitBackend:
    """Test/dev backend. Production should use Redis or another shared atomic store."""

    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def consume(self, key: str, *, limit: int, window_seconds: float) -> RateLimitDecision:
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("invalid rate limit")
        now = time.monotonic()
        async with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            cutoff = now - window_seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry = max(0.0, bucket[0] + window_seconds - now)
                return RateLimitDecision(False, 0, retry)
            bucket.append(now)
            return RateLimitDecision(True, limit - len(bucket), 0.0)

    async def clear(self) -> None:
        async with self._lock:
            self._buckets.clear()
