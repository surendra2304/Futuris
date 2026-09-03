from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Awaitable, Callable, Any

from .models import FailureKind, ProviderResult


class ProviderError(RuntimeError):
    def __init__(self, provider: str, failure: FailureKind, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.provider = provider
        self.failure = failure
        self.retryable = retryable


@dataclass
class ProviderHealth:
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    last_success_monotonic: float | None = None
    cooldown_until: float = 0.0


class ProviderCircuit:
    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.health = ProviderHealth()
        self._lock = asyncio.Lock()

    async def before_call(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if now < self.health.cooldown_until:
                raise ProviderError("circuit", FailureKind.PROVIDER_UNAVAILABLE, "provider cooldown", retryable=True)

    async def record_success(self) -> None:
        async with self._lock:
            self.health.successes += 1
            self.health.consecutive_failures = 0
            self.health.last_success_monotonic = time.monotonic()
            self.health.cooldown_until = 0.0

    async def record_failure(self) -> None:
        async with self._lock:
            self.health.failures += 1
            self.health.consecutive_failures += 1
            if self.health.consecutive_failures >= self.failure_threshold:
                self.health.cooldown_until = time.monotonic() + self.cooldown_seconds


class ProviderAdapter:
    def __init__(self, name: str, call: Callable[..., Awaitable[Any]]) -> None:
        self.name = name
        self.call = call
        self.circuit = ProviderCircuit()

    async def invoke(self, **kwargs: Any) -> ProviderResult:
        await self.circuit.before_call()
        started = time.perf_counter()
        request_id = kwargs.pop("request_id", None)
        try:
            output = await self.call(**kwargs)
            await self.circuit.record_success()
            return ProviderResult.success(
                self.name, output, latency_ms=(time.perf_counter() - started) * 1000, request_id=request_id
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            await self.circuit.record_failure()
            return ProviderResult.failure_result(
                self.name, FailureKind.TIMEOUT, retryable=True,
                latency_ms=(time.perf_counter() - started) * 1000, request_id=request_id,
            )
        except PermissionError:
            await self.circuit.record_failure()
            return ProviderResult.failure_result(
                self.name, FailureKind.AUTHENTICATION, retryable=False,
                latency_ms=(time.perf_counter() - started) * 1000, request_id=request_id,
            )
        except ValueError:
            await self.circuit.record_failure()
            return ProviderResult.failure_result(
                self.name, FailureKind.INVALID_REQUEST, retryable=False,
                latency_ms=(time.perf_counter() - started) * 1000, request_id=request_id,
            )
        except Exception as exc:
            await self.circuit.record_failure()
            return ProviderResult.failure_result(
                self.name,
                FailureKind.TRANSIENT,
                retryable=True,
                output={"error_type": type(exc).__name__},
                latency_ms=(time.perf_counter() - started) * 1000,
                request_id=request_id,
            )


class ProviderRouter:
    def __init__(self, providers: list[ProviderAdapter]) -> None:
        if not providers:
            raise ValueError("at least one provider is required")
        self.providers = providers

    async def invoke(self, **kwargs: Any) -> ProviderResult:
        failures: list[ProviderResult] = []
        for provider in self.providers:
            result = await provider.invoke(**kwargs)
            if result.ok:
                return result
            failures.append(result)
            if result.failure in {FailureKind.AUTHENTICATION, FailureKind.INVALID_REQUEST}:
                return result
        return failures[-1]
