"""Unit tests for asynchronous circuit breaker."""

import asyncio

import pytest

from futuris.infra.circuit_breaker import (
    AsyncCircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    compute_backoff_with_jitter,
)


@pytest.mark.asyncio
async def test_circuit_breaker_initial_state_closed():
    breaker = AsyncCircuitBreaker(
        name="test_service_initial", failure_threshold=2, recovery_timeout=0.5
    )
    assert breaker.state == CircuitState.CLOSED
    assert breaker.failure_count == 0

    async def successful_call():
        return "success"

    result = await breaker.call(successful_call)
    assert result == "success"
    assert breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_circuit_breaker_trips_to_open_on_failures():
    breaker = AsyncCircuitBreaker(
        name="test_service_trip", failure_threshold=2, recovery_timeout=0.5
    )

    async def failing_call():
        raise RuntimeError("Service failure")

    with pytest.raises(RuntimeError):
        await breaker.call(failing_call)
    assert breaker.state == CircuitState.CLOSED
    assert breaker.failure_count == 1

    with pytest.raises(RuntimeError):
        await breaker.call(failing_call)
    assert breaker.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_circuit_breaker_rejects_when_open():
    breaker = AsyncCircuitBreaker(
        name="test_service_reject", failure_threshold=1, recovery_timeout=1.0
    )

    async def failing_call():
        raise RuntimeError("Immediate failure")

    with pytest.raises(RuntimeError):
        await breaker.call(failing_call)
    assert breaker.state == CircuitState.OPEN

    async def normal_call():
        return "ok"

    with pytest.raises(CircuitBreakerOpenError) as exc_info:
        await breaker.call(normal_call)
    assert "is OPEN" in str(exc_info.value)
    assert exc_info.value.retry_after > 0


@pytest.mark.asyncio
async def test_circuit_breaker_fallback_when_open_and_failing():
    breaker = AsyncCircuitBreaker(
        name="test_service_fallback", failure_threshold=1, recovery_timeout=0.5
    )

    async def failing_call():
        raise ConnectionError("Timeout")

    fallback_called = False

    def fallback():
        nonlocal fallback_called
        fallback_called = True
        return {"status": "degraded", "data": "cached_state"}

    # First call trips the breaker and invokes fallback
    res = await breaker.call(failing_call, fallback=fallback)
    assert res == {"status": "degraded", "data": "cached_state"}
    assert fallback_called is True
    assert breaker.state == CircuitState.OPEN

    # Subsequent call while OPEN also invokes fallback without executing call
    res2 = await breaker.call(failing_call, fallback=fallback)
    assert res2 == {"status": "degraded", "data": "cached_state"}


@pytest.mark.asyncio
async def test_circuit_breaker_recovers_to_closed_via_half_open():
    breaker = AsyncCircuitBreaker(
        name="test_service_recovery",
        failure_threshold=1,
        recovery_timeout=0.2,
        half_open_success_threshold=2,
    )

    async def failing_call():
        raise ValueError("Error")

    with pytest.raises(ValueError):
        await breaker.call(failing_call)
    assert breaker.state == CircuitState.OPEN

    # Wait for recovery timeout
    await asyncio.sleep(0.25)

    async def recovered_call():
        return "recovered"

    # First success in HALF_OPEN
    res1 = await breaker.call(recovered_call)
    assert res1 == "recovered"
    assert breaker.state == CircuitState.HALF_OPEN
    assert breaker.consecutive_successes == 1

    # Second success closes the circuit
    res2 = await breaker.call(recovered_call)
    assert res2 == "recovered"
    assert breaker.state == CircuitState.CLOSED
    assert breaker.failure_count == 0


def test_compute_backoff_with_jitter():
    delay1 = compute_backoff_with_jitter(attempt=1, base_delay=0.1, max_delay=1.0)
    assert 0.1 <= delay1 <= 0.2

    delay2 = compute_backoff_with_jitter(attempt=3, base_delay=0.1, max_delay=1.0)
    assert 0.4 <= delay2 <= 0.8


def test_circuit_breaker_registry_stats():
    breaker = AsyncCircuitBreaker(name="test_stats_service")
    stats = breaker.get_stats()
    assert stats["name"] == "test_stats_service"
    assert stats["state"] == "CLOSED"
    assert "total_requests" in stats

    all_stats = AsyncCircuitBreaker.get_registry_stats()
    assert "test_stats_service" in all_stats
