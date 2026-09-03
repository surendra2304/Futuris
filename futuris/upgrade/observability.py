from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass


@dataclass
class Metric:
    count: int = 0
    total_ms: float = 0.0
    failures: int = 0

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.count if self.count else 0.0


class Metrics:
    def __init__(self) -> None:
        self._metrics: dict[str, Metric] = defaultdict(Metric)
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def timer(self, name: str):
        started = time.perf_counter()
        failed = False
        try:
            yield
        except Exception:
            failed = True
            raise
        finally:
            async with self._lock:
                metric = self._metrics[name]
                metric.count += 1
                metric.total_ms += (time.perf_counter() - started) * 1000
                if failed:
                    metric.failures += 1

    async def snapshot(self) -> dict[str, dict[str, float]]:
        async with self._lock:
            return {
                key: {
                    "count": float(value.count),
                    "avg_ms": value.avg_ms,
                    "failures": float(value.failures),
                }
                for key, value in self._metrics.items()
            }
