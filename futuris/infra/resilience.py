"""Resilience primitives: per-peer circuit breakers and bounded retry.

FUTURIS depends on eight peer agents. When one of them is down, an
unprotected caller pays the full client timeout on every request, which turns
a single dead peer into a latency amplifier for the whole mesh (measured at
61s for one refresh pass before the refresh budget existed).

A breaker per peer changes that: after ``failure_threshold`` consecutive
failures the circuit opens and calls fail immediately for
``recovery_timeout`` seconds, then a single probe is allowed through. A
successful probe closes the circuit; a failed probe re-opens it. The state is
exposed so ``/v1/self/status`` can report which peers the agent has isolated
and whether it has recovered, rather than pretending everything is fine.
"""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar

from futuris.infra.logging import get_logger
from futuris.infra.metrics import PEER_CALL_TOTAL, PEER_CIRCUIT_STATE

logger = get_logger("futuris.infra.resilience")

T = TypeVar("T")


class CircuitState(StrEnum):
    """Lifecycle of a peer circuit."""

    CLOSED = "closed"  # healthy: calls pass through
    OPEN = "open"  # failing: calls are refused immediately
    HALF_OPEN = "half_open"  # probing: one call is allowed to test recovery


class CircuitOpenError(RuntimeError):
    """Raised when a call is refused because the peer circuit is open."""

    def __init__(self, name: str, retry_after_seconds: float) -> None:
        self.name = name
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"circuit '{name}' is open; retrying in {retry_after_seconds:.1f}s"
        )


@dataclass
class CircuitBreaker:
    """A single peer's failure detector and recovery probe."""

    name: str
    failure_threshold: int = 3
    recovery_timeout: float = 30.0
    half_open_max_calls: int = 1
    clock: Callable[[], float] = time.monotonic

    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    total_calls: int = 0
    total_failures: int = 0
    total_short_circuits: int = 0
    total_recoveries: int = 0
    opened_at: float | None = None
    last_error: str | None = None
    _half_open_calls: int = field(default=0, repr=False)

    # ── state transitions ──────────────────────────────────────────────────

    def _seconds_until_probe(self) -> float:
        if self.opened_at is None:
            return 0.0
        elapsed = self.clock() - self.opened_at
        return max(0.0, self.recovery_timeout - elapsed)

    def allow_request(self) -> bool:
        """Decide whether a call may proceed, moving OPEN -> HALF_OPEN when due."""
        if self.state is CircuitState.CLOSED:
            return True
        if self.state is CircuitState.HALF_OPEN:
            if self._half_open_calls < self.half_open_max_calls:
                self._half_open_calls += 1
                return True
            return False
        # OPEN
        if self._seconds_until_probe() > 0:
            return False
        self.state = CircuitState.HALF_OPEN
        self._half_open_calls = 1
        logger.info("circuit_half_opened", peer=self.name)
        self._publish_state()
        return True

    def record_success(self) -> None:
        """A successful call closes the circuit and clears the failure streak."""
        was_probing = self.state is not CircuitState.CLOSED
        self.consecutive_failures = 0
        self._half_open_calls = 0
        self.state = CircuitState.CLOSED
        self.opened_at = None
        self.last_error = None
        if was_probing:
            self.total_recoveries += 1
            logger.info("circuit_closed_after_recovery", peer=self.name)
        self._publish_state()

    def record_failure(self, error: str | None = None) -> None:
        """A failed call counts toward opening the circuit."""
        self.total_failures += 1
        self.consecutive_failures += 1
        self.last_error = error
        if (
            self.state is CircuitState.HALF_OPEN
            or self.consecutive_failures >= self.failure_threshold
        ):
            if self.state is not CircuitState.OPEN:
                logger.warning(
                    "circuit_opened",
                    peer=self.name,
                    consecutive_failures=self.consecutive_failures,
                    error=error,
                )
            self.state = CircuitState.OPEN
            self.opened_at = self.clock()
            self._half_open_calls = 0
        self._publish_state()

    def _publish_state(self) -> None:
        PEER_CIRCUIT_STATE.labels(peer=self.name).set(
            {
                CircuitState.CLOSED: 0,
                CircuitState.HALF_OPEN: 1,
                CircuitState.OPEN: 2,
            }[self.state]
        )

    def reset(self) -> None:
        """Force the circuit closed (used by the self-healing supervisor)."""
        self.consecutive_failures = 0
        self.state = CircuitState.CLOSED
        self.opened_at = None
        self._half_open_calls = 0
        self._publish_state()

    # ── reporting ──────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Expose the breaker's state for health reporting."""
        return {
            "peer": self.name,
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "total_calls": self.total_calls,
            "total_failures": self.total_failures,
            "total_short_circuits": self.total_short_circuits,
            "total_recoveries": self.total_recoveries,
            "retry_in_seconds": round(self._seconds_until_probe(), 1)
            if self.state is CircuitState.OPEN
            else 0.0,
            "last_error": self.last_error,
        }


class CircuitBreakerRegistry:
    """Owns one breaker per peer and reports the fleet's isolation state."""

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
    ) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

    def for_peer(self, name: str) -> CircuitBreaker:
        if name not in self._breakers:
            breaker = CircuitBreaker(
                name=name,
                failure_threshold=self.failure_threshold,
                recovery_timeout=self.recovery_timeout,
            )
            breaker._publish_state()
            self._breakers[name] = breaker
        return self._breakers[name]

    def peers(self) -> list[str]:
        return sorted(self._breakers)

    def snapshots(self) -> list[dict[str, Any]]:
        return [breaker.snapshot() for breaker in self._breakers.values()]

    def unhealthy(self) -> list[str]:
        return [
            name
            for name, breaker in self._breakers.items()
            if breaker.state is not CircuitState.CLOSED
        ]

    def reset(self, name: str | None = None) -> list[str]:
        """Force-close one breaker or all of them; returns the peers affected."""
        targets = [name] if name else list(self._breakers)
        for target in targets:
            self._breakers[target].reset()
        return [t for t in targets if t is not None]


# The earlier name for CircuitOpenError; kept so existing imports keep working.
CircuitOpen = CircuitOpenError



async def guarded_call(
    registry: CircuitBreakerRegistry,
    peer: str,
    call: Callable[[], Awaitable[T]],
    *,
    fallback: T | None = None,
    on_failure: Callable[[BaseException], None] | None = None,
) -> T | None:
    """Run ``call`` under the peer's breaker.

    Returns ``fallback`` when the breaker refuses the call or when the call
    itself fails; never raises for peer problems, because callers of a peer
    must degrade rather than crash. Programmer errors are not swallowed: only
    ``Exception`` subclasses are caught, and they are recorded on the breaker.
    """
    breaker = registry.for_peer(peer)
    if not breaker.allow_request():
        breaker.total_short_circuits += 1
        PEER_CALL_TOTAL.labels(peer=peer, outcome="short_circuited").inc()
        return fallback

    breaker.total_calls += 1
    try:
        result = await call()
    except Exception as exc:
        breaker.record_failure(f"{type(exc).__name__}: {exc}")
        PEER_CALL_TOTAL.labels(peer=peer, outcome="failed").inc()
        if on_failure is not None:
            on_failure(exc)
        return fallback

    breaker.record_success()
    PEER_CALL_TOTAL.labels(peer=peer, outcome="ok").inc()
    return result


# Process-wide registry for the peer mesh.
peer_circuits = CircuitBreakerRegistry()
