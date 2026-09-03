from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.25,
    max_delay: float = 5.0,
    jitter: float = 0.20,
    is_retryable: Callable[[Exception], bool] | None = None,
) -> T:
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return await operation()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_exc = exc
            retryable = is_retryable(exc) if is_retryable else True
            if not retryable or attempt == attempts - 1:
                raise
            delay = min(max_delay, base_delay * (2 ** attempt))
            delay *= 1.0 + random.uniform(-jitter, jitter)
            await asyncio.sleep(max(0.0, delay))
    assert last_exc is not None
    raise last_exc
