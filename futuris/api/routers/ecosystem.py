"""Ecosystem integration router for FRIDAY Universe peer agents and health matrix."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from futuris.demo.seed import DemoSeeder
from futuris.ecosystem.adapters import ecosystem_adapter
from futuris.infra.auth import AuthUser, RequireViewer
from futuris.infra.logging import get_logger

logger = get_logger("futuris.api.ecosystem")

router = APIRouter(prefix="/v1/ecosystem", tags=["Ecosystem"])


class PeerAgentInfo(BaseModel):
    name: str
    role: str
    url: str
    status: str
    latency_ms: float | None
    last_interaction: str
    capabilities: list[str]


class EcosystemOverviewResponse(BaseModel):
    peers: list[PeerAgentInfo]
    total_online: int
    total_peers: int
    timestamp: datetime


class SeedResponse(BaseModel):
    status: str
    message: str


@router.get("/peers", response_model=EcosystemOverviewResponse, summary="Probe FRIDAY Universe Peer Agents")
async def get_ecosystem_peers(user: RequireViewer) -> Any:
    """Check connectivity, roundtrip latency, and integration capabilities of all FRIDAY Universe microservices."""
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
    user: RequireViewer,
) -> Any:
    """Manually trigger historical 180-day telemetry and forecast seed."""
    logger.info("manual_demo_seed_triggered", triggered_by=user.label)
    
    async def _do_seed():
        seeder = DemoSeeder(seed=42)
        await seeder.run()
        logger.info("background_demo_seed_finished")

    background_tasks.add_task(_do_seed)
    return {
        "status": "accepted",
        "message": "Demo seeding initiated in background. Forecast workspace and calibration curves will populate shortly.",
    }
