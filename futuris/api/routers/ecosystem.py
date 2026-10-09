"""Ecosystem integration router for FRIDAY Universe peer agents and health matrix."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from futuris.api.deps import get_db_session
from futuris.demo.seed import DemoSeeder
from futuris.ecosystem.adapters import ecosystem_adapter
from futuris.infra.audit import AuditLogger
from futuris.infra.auth import AllowAnonymousRead, RequireAdmin
from futuris.infra.logging import get_logger

logger = get_logger("futuris.api.ecosystem")

router = APIRouter(prefix="/v1/ecosystem", tags=["Ecosystem"])


class PeerAgentInfo(BaseModel):
    name: str
    role: str
    url: str
    status: str
    latency_ms: float | None
    evidence_class: str
    http_status: int | None
    observed_at: datetime
    capabilities: list[str]


class EcosystemOverviewResponse(BaseModel):
    peers: list[PeerAgentInfo]
    total_online: int
    total_peers: int
    timestamp: datetime


class SeedResponse(BaseModel):
    status: str
    message: str
    started_at: datetime | None = None
    completed_runs: int = 0


class _SeedGuard:
    """At most one demo seed per process, and no queue of duplicate seeds.

    A seed rewrites 180 days of synthetic telemetry, forecasts, scenarios and
    calibrations in a single session, so it is the heaviest writer in the system
    and it holds the SQLite write lock for the length of the run.  Without a
    guard every trigger (a retry loop, an operator double-click, an adversarial
    client) started *another* full re-seed: measured under an 8-way concurrent
    run this starved unrelated writers, which surfaced as ``503 storage_busy``
    on ``POST /v1/predictions/predict``.  Triggers that arrive while a seed is
    running are now answered instead of stacked.
    """

    def __init__(self) -> None:
        self._active = False
        self.started_at: datetime | None = None
        self.completed_runs = 0
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._active

    def claim(self) -> bool:
        """Take the slot, or report that someone else has it.

        Called synchronously from the request path, so two triggers that arrive
        in the same tick cannot both queue a seed.
        """
        if self._active:
            return False
        self._active = True
        return True

    async def run_once(self, seeder_factory: Any) -> None:
        """Run one seed. The slot must already be claimed by the caller."""
        self.started_at = datetime.now(UTC)
        logger.info("background_demo_seed_started", started_at=self.started_at.isoformat())
        try:
            seeder = seeder_factory()
            await seeder.run()
            self.completed_runs += 1
            self.last_error = None
            logger.info("background_demo_seed_finished", completed_runs=self.completed_runs)
        except Exception as exc:  # noqa: BLE001 - a failed seed must not kill the loop
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.error("background_demo_seed_failed", error=self.last_error)
        finally:
            self._active = False


seed_guard = _SeedGuard()


@router.get(
    "/peers",
    response_model=EcosystemOverviewResponse,
    summary="Probe FRIDAY Universe Peer Agents",
)
async def get_ecosystem_peers(user: AllowAnonymousRead) -> Any:
    _ = user  # auth dependency; identity handled by the role guard
    """Check connectivity, latency, and capabilities of FRIDAY Universe peers."""
    peers = await ecosystem_adapter.probe_peers()
    online_count = sum(1 for p in peers if p["status"] == "online")

    return {
        "peers": peers,
        "total_online": online_count,
        "total_peers": len(peers),
        "timestamp": datetime.now(UTC),
    }


@router.post("/seed", response_model=SeedResponse, summary="Seed 180-Day Benchmark Workspace")
async def seed_workspace(
    background_tasks: BackgroundTasks,
    user: RequireAdmin,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Manually trigger historical 180-day telemetry and forecast seed.

    Idempotent by construction: while a seed runs, further triggers are answered
    with ``already_running`` rather than queueing another 180-day rewrite.
    """
    logger.info("manual_demo_seed_triggered", triggered_by=user.label)

    if not seed_guard.claim():
        # A rejected trigger mutates nothing: no audit row, and no write-lock
        # acquisition that would contend with the seed that is already running.
        return SeedResponse(
            status="already_running",
            message=(
                "A demo seeding run is already in progress; this trigger was not queued "
                "because seeding rewrites the same 180-day history."
            ),
            started_at=seed_guard.started_at,
            completed_runs=seed_guard.completed_runs,
        )

    # Seeding rewrites the workspace: the accepted trigger is a mutating action
    # and is audited (written before the background seed takes the write lock).
    await AuditLogger(session).log_mutation(
        actor_label=user.label,
        action="trigger_demo_seed",
        entity="workspace",
        entity_id="demo_seed",
        payload={"triggered_by": user.label},
    )

    background_tasks.add_task(seed_guard.run_once, lambda: DemoSeeder(seed=42))
    return SeedResponse(
        status="accepted",
        message="Demo seeding initiated in background. Forecast workspace and calibration "
        "curves will populate shortly.",
        started_at=datetime.now(UTC),
        completed_runs=seed_guard.completed_runs,
    )
