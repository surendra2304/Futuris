"""Thread-safe in-memory TTL/LRU caching with stats tracking and decorator support."""

import functools
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from futuris.infra.logging import get_logger

logger = get_logger("futuris.infra.cache")

T = TypeVar("T")


@dataclass
class CacheEntry:
    value: Any
    expires_at: float
    created_at: float


class TTLCache:
    """Thread-safe in-memory Least Recently Used (LRU) cache with Time-To-Live (TTL)."""

    def __init__(self, max_size: int = 1000, default_ttl_seconds: float = 60.0):
        self.max_size = max_size
        self.default_ttl = default_ttl_seconds
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()

        self.hits: int = 0
        self.misses: int = 0
        self.evictions: int = 0
        self.expirations: int = 0

    def get(self, key: str) -> Any | None:
        """Retrieve unexpired entry by key; returns None on miss or expiry."""
        with self._lock:
            now = time.monotonic()
            if key not in self._cache:
                self.misses += 1
                return None

            entry = self._cache[key]
            if now > entry.expires_at:
                del self._cache[key]
                self.expirations += 1
                self.misses += 1
                return None

            # Move to end (most recently used)
            self._cache.move_to_end(key)
            self.hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl_seconds: float | None = None) -> None:
        """Store key-value pair with designated TTL in seconds."""
        with self._lock:
            now = time.monotonic()
            ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
            expires_at = now + ttl

            if key in self._cache:
                self._cache[key] = CacheEntry(value=value, expires_at=expires_at, created_at=now)
                self._cache.move_to_end(key)
                return

            if len(self._cache) >= self.max_size:
                # Evict oldest entry (LRU)
                oldest_key, _ = self._cache.popitem(last=False)
                self.evictions += 1
                logger.debug("ttl_cache_evicted_lru", evicted_key=oldest_key)

            self._cache[key] = CacheEntry(value=value, expires_at=expires_at, created_at=now)

    def delete(self, key: str) -> bool:
        """Remove key from cache if present."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> None:
        """Purge all entries from the cache."""
        with self._lock:
            self._cache.clear()

    def prune_expired(self) -> int:
        """Purge all expired entries across the cache; returns count removed."""
        with self._lock:
            now = time.monotonic()
            expired_keys = [k for k, v in self._cache.items() if now > v.expires_at]
            for k in expired_keys:
                del self._cache[k]
                self.expirations += 1
            return len(expired_keys)

    def stats(self) -> dict[str, Any]:
        """Expose operational telemetry for cache diagnostics."""
        with self._lock:
            total_lookups = self.hits + self.misses
            hit_ratio = round((self.hits / total_lookups), 4) if total_lookups > 0 else 0.0
            return {
                "size": len(self._cache),
                "max_size": self.max_size,
                "hits": self.hits,
                "misses": self.misses,
                "hit_ratio": hit_ratio,
                "evictions": self.evictions,
                "expirations": self.expirations,
            }


# Global in-memory cache instance for forecast and prediction queries
forecast_cache = TTLCache(max_size=2000, default_ttl_seconds=30.0)


def cached_async(
    cache: TTLCache,
    ttl_seconds: float = 30.0,
    key_builder: Callable[..., str] | None = None,
):
    """Decorator to cache asynchronous coroutine results with in-memory TTL."""

    def decorator(func: Callable[..., Any]):
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any):
            if key_builder:
                key = key_builder(*args, **kwargs)
            else:
                key = f"{func.__name__}:{str(args)}:{str(sorted(kwargs.items()))}"

            cached_val = cache.get(key)
            if cached_val is not None:
                return cached_val

            result = await func(*args, **kwargs)
            cache.set(key, result, ttl_seconds=ttl_seconds)
            return result

        return wrapper

    return decorator
