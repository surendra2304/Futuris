"""Unit tests for thread-safe in-memory TTLCache."""

import asyncio
import time
import pytest

from futuris.infra.cache import TTLCache, cached_async


def test_ttl_cache_basic_set_get():
    cache = TTLCache(max_size=10, default_ttl_seconds=5.0)
    cache.set("key1", "val1")
    assert cache.get("key1") == "val1"
    assert cache.get("nonexistent") is None


def test_ttl_cache_expiration():
    cache = TTLCache(max_size=10, default_ttl_seconds=0.1)
    cache.set("short_lived", "data", ttl_seconds=0.1)
    assert cache.get("short_lived") == "data"

    time.sleep(0.15)
    assert cache.get("short_lived") is None
    assert cache.expirations >= 1


def test_ttl_cache_lru_eviction():
    cache = TTLCache(max_size=2, default_ttl_seconds=10.0)
    cache.set("k1", "v1")
    cache.set("k2", "v2")

    # Access k1 to make k2 least recently used
    assert cache.get("k1") == "v1"

    # Add k3, which should evict k2
    cache.set("k3", "v3")
    assert cache.get("k2") is None
    assert cache.get("k1") == "v1"
    assert cache.get("k3") == "v3"
    assert cache.evictions == 1


def test_ttl_cache_delete_and_clear():
    cache = TTLCache(max_size=10)
    cache.set("a", 1)
    cache.set("b", 2)

    assert cache.delete("a") is True
    assert cache.delete("a") is False
    assert cache.get("a") is None

    cache.clear()
    assert cache.get("b") is None
    assert cache.stats()["size"] == 0


def test_ttl_cache_prune_expired():
    cache = TTLCache(max_size=10, default_ttl_seconds=0.05)
    cache.set("k1", "v1")
    cache.set("k2", "v2")

    time.sleep(0.1)
    pruned = cache.prune_expired()
    assert pruned == 2
    assert cache.stats()["size"] == 0


def test_ttl_cache_stats():
    cache = TTLCache(max_size=5)
    cache.set("hit_key", "hit_val")
    cache.get("hit_key")
    cache.get("miss_key")

    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["hit_ratio"] == 0.5


@pytest.mark.asyncio
async def test_cached_async_decorator():
    cache = TTLCache(max_size=10, default_ttl_seconds=1.0)
    call_count = 0

    @cached_async(cache=cache, ttl_seconds=1.0)
    async def fetch_expensive_metric(metric_id: str):
        nonlocal call_count
        call_count += 1
        return f"result_for_{metric_id}"

    res1 = await fetch_expensive_metric("cpu_load")
    assert res1 == "result_for_cpu_load"
    assert call_count == 1

    # Second call hits cache
    res2 = await fetch_expensive_metric("cpu_load")
    assert res2 == "result_for_cpu_load"
    assert call_count == 1

    # Different key invokes function
    res3 = await fetch_expensive_metric("memory_load")
    assert res3 == "result_for_memory_load"
    assert call_count == 2
