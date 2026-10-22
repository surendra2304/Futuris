"""Self-assessment and self-healing supervisor.

This is the agent's own picture of itself. Every claim it makes is measured at
the moment it is asked: a subsystem is only reported ``ok`` when a real check
just succeeded, and anything that cannot be checked is reported as
``unconfigured`` or ``unknown`` rather than assumed healthy.

The supervisor also acts. Each recovery action is applied only when its
precondition is observed, is recorded as a structured audit entry, and is
counted in ``SELF_HEALING_ACTIONS_TOTAL``. The status endpoint reports both the
current state and the history of what was healed, so a client can tell a system
that was always healthy from one that keeps repairing itself.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, text

from futuris.core.enums import EvidenceClass
from futuris.infra.config import settings
from futuris.infra.events import event_emitter
from futuris.infra.logging import get_logger
from futuris.infra.metrics import DEGRADED_SUBSYSTEMS, SELF_HEALING_ACTIONS_TOTAL
from futuris.infra.resilience import CircuitState, peer_circuits
from futuris.storage.db import async_session_factory, engine, ensure_storage_directories

logger = get_logger("futuris.infra.self_healing")

HEALTH_STATES = ("ok", "degraded", "down", "unconfigured", "unknown")

#: Total wall-clock budget for one self-status measurement. /v1/self/status is
#: the liveness endpoint the mesh polls constantly, so it must answer even
#: while a forecast fit saturates the CPU: checks that cannot finish inside
#: the budget report ``unknown`` instead of stalling the answer.
STATUS_MEASUREMENT_BUDGET_SECONDS = 3.0

#: How long an assembled status payload may be served from cache. Repeated
#: polls (the UI refreshes every 45s, harnesses poll continuously) become
#: instant; the payload keeps the ``checked_at`` of its measurement, so a
#: cached answer never claims to be fresher than it is.
STATUS_CACHE_SECONDS = 2.0


@dataclass
class SubsystemCheck:
    """One measured statement about the agent's own condition."""

    name: str
    state: str
    detail: str
    evidence_class: str
    measured_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "subsystem": self.name,
            "state": self.state,
            "detail": self.detail,
            "evidence_class": self.evidence_class,
            "measured_at": self.measured_at.isoformat(),
            "data": self.data,
        }


class SelfHealingSupervisor:
    """Measures subsystem health and repairs what it can observe broken."""

    def __init__(self) -> None:
        self.healing_history: list[dict[str, Any]] = []
        self.max_history = 200
        self._status_cache: tuple[float, Any, dict[str, Any]] | None = None

    # ── measurement ────────────────────────────────────────────────────────

    async def check_database(self) -> SubsystemCheck:
        """Round-trip the configured database and report what it answers."""
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return SubsystemCheck(
                name="database",
                state="ok",
                detail="connection and round-trip succeeded",
                evidence_class=EvidenceClass.LIVE.value,
                data={"dialect": engine.dialect.name},
            )
        except Exception as exc:
            return SubsystemCheck(
                name="database",
                state="down",
                detail=f"{type(exc).__name__}: {exc}",
                evidence_class=EvidenceClass.LIVE.value,
                data={"dialect": getattr(engine.dialect, "name", "unknown")},
            )

    async def check_tables(self) -> SubsystemCheck:
        """Verify the schema the application needs is actually present."""
        from futuris.storage.models import Base

        expected = {table.name for table in Base.metadata.sorted_tables}
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                    if engine.dialect.name == "sqlite"
                    else text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                )
                present = {row[0] for row in result}
        except Exception as exc:
            return SubsystemCheck(
                name="schema",
                state="unknown",
                detail=f"could not enumerate tables: {type(exc).__name__}: {exc}",
                evidence_class=EvidenceClass.LIVE.value,
            )

        missing = sorted(expected - present)
        if missing:
            return SubsystemCheck(
                name="schema",
                state="down",
                detail=f"{len(missing)} required tables are missing",
                evidence_class=EvidenceClass.LIVE.value,
                data={"missing_tables": missing},
            )
        return SubsystemCheck(
            name="schema",
            state="ok",
            detail=f"all {len(expected)} required tables present",
            evidence_class=EvidenceClass.LIVE.value,
        )

    async def check_outbound_delivery(self) -> SubsystemCheck:
        """Report webhook delivery health from the emitter's own counters."""
        metrics = event_emitter.delivery_stats()
        if metrics["attempted"] == 0:
            return SubsystemCheck(
                name="outbound_webhooks",
                state="unknown",
                detail="no webhook deliveries attempted yet",
                evidence_class=EvidenceClass.LIVE.value,
                data=metrics,
            )
        success_rate = metrics["delivered"] / metrics["attempted"]
        state = "ok" if metrics["failed"] == 0 else ("degraded" if success_rate > 0.5 else "down")
        return SubsystemCheck(
            name="outbound_webhooks",
            state=state,
            detail=f"{metrics['delivered']}/{metrics['attempted']} deliveries succeeded",
            evidence_class=EvidenceClass.LIVE.value,
            data={**metrics, "success_rate": round(success_rate, 3)},
        )

    def check_peers(self) -> SubsystemCheck:
        """Report the peer mesh from the circuit breakers' measured outcomes."""
        snapshots = peer_circuits.snapshots()
        if not snapshots:
            return SubsystemCheck(
                name="peer_mesh",
                state="unknown",
                detail="no peer calls attempted yet",
                evidence_class=EvidenceClass.LIVE.value,
            )
        open_peers = [s["peer"] for s in snapshots if s["state"] == CircuitState.OPEN.value]
        probing = [s["peer"] for s in snapshots if s["state"] == CircuitState.HALF_OPEN.value]
        if not open_peers and not probing:
            state = "ok"
        elif len(open_peers) < len(snapshots):
            state = "degraded"
        else:
            state = "down"
        return SubsystemCheck(
            name="peer_mesh",
            state=state,
            detail=(
                f"{len(snapshots)} peers tracked, {len(open_peers)} isolated, "
                f"{len(probing)} probing recovery"
            ),
            evidence_class=EvidenceClass.LIVE.value,
            data={"peers": snapshots, "isolated": open_peers},
        )

    def check_scheduler(self) -> SubsystemCheck:
        """Report whether the unattended loop is actually running."""
        from futuris.api import app as app_module

        scheduler = getattr(app_module, "_running_scheduler", None)
        if not settings.SCHEDULER_ENABLED:
            return SubsystemCheck(
                name="scheduler",
                state="unconfigured",
                detail="SCHEDULER_ENABLED=false; no unattended jobs are running",
                evidence_class=EvidenceClass.LIVE.value,
            )
        if scheduler is None:
            return SubsystemCheck(
                name="scheduler",
                state="down",
                detail="scheduler is enabled but not running in this process",
                evidence_class=EvidenceClass.LIVE.value,
            )
        running = bool(getattr(scheduler.scheduler, "running", False))
        return SubsystemCheck(
            name="scheduler",
            state="ok" if running else "down",
            detail=f"{len(scheduler.subscriptions)} subscriptions, running={running}",
            evidence_class=EvidenceClass.LIVE.value,
            data={"subscriptions": [s.target for s in scheduler.subscriptions]},
        )

    async def check_integrity(self, *, sample_limit: int = 200) -> SubsystemCheck:
        """Report whether served evidence hashes are real and verifiable.

        The check samples the most recent ``sample_limit`` evidence rows rather
        than scanning the whole table: /v1/self/status is the liveness endpoint
        the mesh polls constantly, and an unbounded scan grows with the number
        of forecasts until the endpoint starves the event loop under load
        (measured: an 8.1s liveness stall behind a concurrent forecast burst).
        The sample is reported alongside the verdict so the answer says what it
        actually measured.
        """
        from sqlalchemy import select

        from futuris.core.hashing import is_real_hash
        from futuris.storage.models import EvidenceRefModel

        try:
            async with async_session_factory() as session:
                total = (
                    await session.execute(select(func.count(EvidenceRefModel.evidence_id)))
                ).scalar_one()
                rows = (
                    await session.execute(
                        select(EvidenceRefModel)
                        .order_by(EvidenceRefModel.as_of.desc())
                        .limit(sample_limit)
                    )
                ).scalars().all()
        except Exception as exc:
            return SubsystemCheck(
                name="evidence_integrity",
                state="unknown",
                detail=f"could not read evidence: {type(exc).__name__}: {exc}",
                evidence_class=EvidenceClass.LIVE.value,
            )

        if not rows:
            return SubsystemCheck(
                name="evidence_integrity",
                state="unknown",
                detail="no evidence references stored yet",
                evidence_class=EvidenceClass.LIVE.value,
                data={"evidence_refs": 0},
            )

        invalid = [str(r.evidence_id) for r in rows if not is_real_hash(r.content_hash)]
        if invalid:
            return SubsystemCheck(
                name="evidence_integrity",
                state="down",
                detail=(
                    f"{len(invalid)}/{len(rows)} sampled evidence hashes are not real digests"
                ),
                evidence_class=EvidenceClass.LIVE.value,
                data={
                    "invalid_evidence_ids": invalid[:10],
                    "evidence_refs_total": total,
                    "evidence_refs_sampled": len(rows),
                },
            )
        return SubsystemCheck(
            name="evidence_integrity",
            state="ok",
            detail=(
                f"all {len(rows)} sampled evidence hashes are real SHA-256 digests"
                + (f" (of {total} total)" if total > len(rows) else "")
            ),
            evidence_class=EvidenceClass.LIVE.value,
            data={
                "evidence_refs_total": total,
                "evidence_refs_sampled": len(rows),
            },
        )

    async def check_agent_loop(self) -> SubsystemCheck:
        """Report whether the multi-agent surface is reachable in this process."""
        from futuris.api.app import app, iter_route_paths

        routes = iter_route_paths(app)
        required = {
            "/v1/friday/forecast",
            "/v1/friday/delegate",
            "/v1/task/execute",
            "/v1/self/status",
        }
        missing = sorted(required - routes)
        if missing:
            return SubsystemCheck(
                name="agent_surface",
                state="down",
                detail=f"missing routes: {', '.join(missing)}",
                evidence_class=EvidenceClass.LIVE.value,
            )
        return SubsystemCheck(
            name="agent_surface",
            state="ok",
            detail=f"{len(routes)} routes mounted, agent delegation surface intact",
            evidence_class=EvidenceClass.LIVE.value,
            data={"route_count": len(routes)},
        )

    # ── healing ────────────────────────────────────────────────────────────

    async def status_tables_ok(self) -> list[SubsystemCheck]:
        """Convenience used by tests and callers that only care about storage."""
        return [await self.check_database(), await self.check_tables()]

    async def heal(self, checks: list[SubsystemCheck]) -> list[dict[str, Any]]:
        """Apply recovery actions for the failures that were just observed."""
        actions: list[dict[str, Any]] = []
        by_name = {check.name: check for check in checks}

        if by_name.get("database", SubsystemCheck("", "", "", "")).state == "down":
            ensure_storage_directories()
            actions.append(
                self._record(
                    action="ensure_storage_directories",
                    outcome="applied",
                    detail="re-created the database and object-store directories",
                )
            )

        if by_name.get("schema", SubsystemCheck("", "", "", "")).state == "down":
            from futuris.storage.models import Base

            try:
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                actions.append(
                    self._record(
                        action="create_missing_tables",
                        outcome="applied",
                        detail="created the tables the ORM declares",
                    )
                )
            except Exception as exc:
                actions.append(
                    self._record(
                        action="create_missing_tables",
                        outcome="failed",
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                )

        # Close breakers whose peers have been isolated longer than their
        # cooldown, so the mesh gets a chance to reconnect instead of staying
        # partitioned forever.
        reset_peers = [
            snapshot["peer"]
            for snapshot in peer_circuits.snapshots()
            if snapshot["state"] == CircuitState.OPEN.value
            and snapshot["retry_in_seconds"] == 0.0
            and snapshot["total_recoveries"] > 0
        ]
        for peer in reset_peers:
            peer_circuits.reset(peer)
            actions.append(
                self._record(
                    action="reset_peer_circuit",
                    outcome="applied",
                    detail=f"allowed a fresh probe for '{peer}' after cooldown",
                    peer=peer,
                )
            )

        return actions

    def _record(self, *, action: str, outcome: str, detail: str, **extra: Any) -> dict[str, Any]:
        entry = {
            "action": action,
            "outcome": outcome,
            "detail": detail,
            "at": datetime.now(UTC).isoformat(),
            **extra,
        }
        self.healing_history.append(entry)
        self.healing_history = self.healing_history[-self.max_history :]
        SELF_HEALING_ACTIONS_TOTAL.labels(action=action, outcome=outcome).inc()
        logger.info("self_healing_action", **entry)
        return entry

    # ── reporting ──────────────────────────────────────────────────────────

    async def status(self, *, force: bool = False) -> dict[str, Any]:
        """Measure everything, heal what is broken, and report both.

        Bounded and cached. This is the endpoint the mesh polls for liveness,
        so it must answer promptly even while a forecast fit saturates the
        CPU: the measurement runs under a total wall-clock budget, a check
        that cannot finish in time reports ``unknown`` rather than stalling
        the answer, and the assembled payload is cached briefly so repeated
        polls are instant.

        ``force=True`` bypasses the cache: an explicit healing pass must
        actually measure and actually heal, never replay a recent answer
        (B19). The cache exists for pollers, not for operators.
        """
        now = time.monotonic()
        cached = self._status_cache
        # A cached payload only answers for the engine it was measured against:
        # a payload about one database must never answer for another. The
        # supervisor is process-wide and the test suite swaps its engine per
        # test; production has exactly one engine, so this is a no-op there.
        if not force and cached is not None and now < cached[0] and cached[1] is engine:
            return cached[2]
        payload = await self._measure()
        self._status_cache = (time.monotonic() + STATUS_CACHE_SECONDS, engine, payload)
        return payload

    async def _measure(self) -> dict[str, Any]:
        """Run every subsystem check inside the measurement budget."""
        deadline = time.monotonic() + STATUS_MEASUREMENT_BUDGET_SECONDS
        checks: list[SubsystemCheck] = []

        async def run_timed(name: str, coro) -> None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                checks.append(
                    SubsystemCheck(
                        name=name,
                        state="unknown",
                        detail="not measured: status budget exhausted under load",
                        evidence_class=EvidenceClass.LIVE.value,
                    )
                )
                # The check is skipped, so its coroutine must be closed here; otherwise
                # Python reports "coroutine ... was never awaited" at garbage collection.
                coro.close()
                return
            try:
                checks.append(await asyncio.wait_for(coro, timeout=remaining))
            except TimeoutError:
                checks.append(
                    SubsystemCheck(
                        name=name,
                        state="unknown",
                        detail=(
                            "check did not finish within the status budget under load; "
                            "unknown, not down"
                        ),
                        evidence_class=EvidenceClass.LIVE.value,
                    )
                )

        await run_timed("database", self.check_database())
        await run_timed("schema", self.check_tables())
        await run_timed("evidence_integrity", self.check_integrity())
        # The scheduler and peer-circuit checks are in-memory and fast.
        checks.append(self.check_scheduler())
        checks.append(self.check_peers())
        await run_timed("outbound_webhooks", self.check_outbound_delivery())
        await run_timed("agent_surface", self.check_agent_loop())
        actions = await self.heal(checks)

        # Anything healed in this pass changes the reported state, so measure
        # the affected subsystems again rather than reporting the stale failure.
        if any(a["outcome"] == "applied" for a in actions):
            healed_names = {
                "ensure_storage_directories": "database",
                "create_missing_tables": "schema",
            }
            for action in actions:
                name = healed_names.get(action["action"])
                if name is None:
                    continue
                recheck = await (
                    self.check_database() if name == "database" else self.check_tables()
                )
                checks = [recheck if c.name == name else c for c in checks]

        unhealthy = [c for c in checks if c.state not in ("ok", "unknown", "unconfigured")]
        DEGRADED_SUBSYSTEMS.set(len(unhealthy))

        # An agent that cannot reach its database is not "ok" even if every
        # other subsystem answers.
        if any(c.name == "database" and c.state == "down" for c in checks):
            overall = "down"
        elif unhealthy:
            overall = "degraded"
        elif any(c.state == "ok" for c in checks):
            overall = "ok"
        else:
            overall = "unknown"

        return {
            "status": overall,
            "checked_at": datetime.now(UTC).isoformat(),
            "app_env": settings.APP_ENV,
            "evidence_class": EvidenceClass.LIVE.value,
            "subsystems": [c.as_dict() for c in checks],
            "degraded_subsystems": [c.name for c in unhealthy],
            "self_healing": {
                "actions_this_pass": actions,
                "recent_actions": self.healing_history[-20:],
                "total_actions": len(self.healing_history),
            },
            "peer_circuits": peer_circuits.snapshots(),
        }


self_healing_supervisor = SelfHealingSupervisor()


async def self_healing_loop(interval_seconds: float = 60.0) -> None:
    """Background loop: measure, heal, and keep a heartbeat on record."""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            # force=True: the loop exists to heal, so it must never skip a
            # pass just because a liveness poll refreshed the cache (B19).
            report = await self_healing_supervisor.status(force=True)
            logger.info(
                "self_assessment",
                status=report["status"],
                degraded=report["degraded_subsystems"],
                healed=len(report["self_healing"]["actions_this_pass"]),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let the supervisor die
            logger.error("self_healing_loop_error", error=type(exc).__name__, detail=str(exc))
