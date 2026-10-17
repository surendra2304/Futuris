"""Asynchronous circuit breaker with exponential backoff, jitter, and graceful degradation."""

import asyncio
import random
import time
from collections.abc import Callable, Coroutine
from enum import StrEnum
from typing import Any, TypeVar

from futuris.infra.logging import get_logger

logger = get_logger("futuris.infra.circuit_breaker")

T = TypeVar("T")


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenError(Exception):
    """Raised when an operation is attempted while the circuit is OPEN."""

    def __init__(self, name: str, retry_after: float):
        super().__init__(f"Circuit breaker '{name}' is OPEN. Retry after {retry_after:.1f}s.")
        self.name = name
        self.retry_after = retry_after


def compute_backoff_with_jitter(
    attempt: int, base_delay: float = 0.1, max_delay: float = 2.0, jitter_factor: float = 0.5
) -> float:
    """Compute exponential backoff delay with randomized jitter."""
    exp_delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
    jitter = random.uniform(0, jitter_factor * exp_delay)
    return exp_delay + jitter


class AsyncCircuitBreaker:
    """Production-grade asynchronous circuit breaker protecting external service calls."""

    _registry: dict[str, "AsyncCircuitBreaker"] = {}

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        half_open_success_threshold: int = 2,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_success_threshold = half_open_success_threshold

        self.state: CircuitState = CircuitState.CLOSED
        self.failure_count: int = 0
        self.consecutive_successes: int = 0
        self.last_state_change: float = time.monotonic()
        self.last_failure_time: float | None = None
        self.last_failure_error: str | None = None

        self.total_requests: int = 0
        self.total_failures: int = 0
        self.total_short_circuits: int = 0

        self._lock = asyncio.Lock()
        AsyncCircuitBreaker._registry[name] = self

    @classmethod
    def get_registry_stats(cls) -> dict[str, dict[str, Any]]:
        """Return diagnostic metrics for all registered circuit breakers."""
        return {name: breaker.get_stats() for name, breaker in cls._registry.items()}

    def get_stats(self) -> dict[str, Any]:
        """Return operational telemetry for this circuit breaker."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "consecutive_successes": self.consecutive_successes,
            "total_requests": self.total_requests,
            "total_failures": self.total_failures,
            "total_short_circuits": self.total_short_circuits,
            "last_failure_error": self.last_failure_error,
        }

    async def call(
        self,
        func: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
        fallback: Callable[..., Coroutine[Any, Any, T] | T] | None = None,
        max_retries: int = 0,
        **kwargs: Any,
    ) -> T:
        """Execute an async operation with retries and an optional fallback."""
        async with self._lock:
            self.total_requests += 1
            now = time.monotonic()

            if self.state == CircuitState.OPEN:
                elapsed = now - self.last_state_change
                if elapsed >= self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self.consecutive_successes = 0
                    self.last_state_change = now
                    logger.info("circuit_breaker_half_open", name=self.name)
                else:
                    self.total_short_circuits += 1
                    retry_after = self.recovery_timeout - elapsed
                    if fallback is not None:
                        logger.warning(
                            "circuit_breaker_short_circuit_fallback",
                            name=self.name,
                            retry_after=retry_after,
                        )
                        res = fallback()
                        return await res if asyncio.iscoroutine(res) else res
                    raise CircuitBreakerOpenError(self.name, retry_after)

        # Attempt invocation with exponential backoff if max_retries > 0
        attempts = 0
        while True:
            attempts += 1
            try:
                result = await func(*args, **kwargs)
                await self._on_success()
                return result
            except Exception as exc:
                if attempts <= max_retries:
                    delay = compute_backoff_with_jitter(attempts)
                    logger.warning(
                        "circuit_breaker_retry",
                        name=self.name,
                        attempt=attempts,
                        delay=round(delay, 2),
                        error=str(exc),
                    )
                    await asyncio.sleep(delay)
                    continue

                await self._on_failure(exc)
                if fallback is not None:
                    logger.warning(
                        "circuit_breaker_call_failed_fallback",
                        name=self.name,
                        error=str(exc),
                    )
                    res = fallback()
                    return await res if asyncio.iscoroutine(res) else res
                raise

    async def _on_success(self) -> None:
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.consecutive_successes += 1
                if self.consecutive_successes >= self.half_open_success_threshold:
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    self.consecutive_successes = 0
                    self.last_state_change = time.monotonic()
                    logger.info("circuit_breaker_closed", name=self.name)
            elif self.state == CircuitState.CLOSED:
                self.failure_count = 0

    async def _on_failure(self, exc: Exception) -> None:
        async with self._lock:
            self.total_failures += 1
            self.last_failure_time = time.monotonic()
            self.last_failure_error = f"{type(exc).__name__}: {exc}"

            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
                self.last_state_change = time.monotonic()
                logger.warning(
                    "circuit_breaker_opened_from_half_open",
                    name=self.name,
                    error=self.last_failure_error,
                )
            elif self.state == CircuitState.CLOSED:
                self.failure_count += 1
                if self.failure_count >= self.failure_threshold:
                    self.state = CircuitState.OPEN
                    self.last_state_change = time.monotonic()
                    logger.warning(
                        "circuit_breaker_opened",
                        name=self.name,
                        failures=self.failure_count,
                        error=self.last_failure_error,
                    )
