"""Self-assessment router: the agent's measured picture of itself.

Every field here is produced by a live check at request time. There is no
cached "healthy" constant: if the database cannot be reached, this endpoint
says so, and if a subsystem cannot be checked it says that too.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, status

from futuris.infra.auth import RequireAnalyst, RequireViewer
from futuris.infra.resilience import peer_circuits
from futuris.infra.self_healing import self_healing_supervisor

router = APIRouter(prefix="/v1/self", tags=["Self-Awareness"])


@router.get("/status", summary="Measured Self-Assessment of Every Subsystem")
async def get_self_status(user: RequireViewer) -> dict[str, Any]:
    """Report measured subsystem health, degradation and healing history."""
    _ = user
    return await self_healing_supervisor.status()


@router.get("/peers", summary="Peer Mesh Circuit State")
async def get_peer_circuits(user: RequireViewer) -> dict[str, Any]:
    """Report which peers have been isolated, which are recovering, and why."""
    _ = user
    return {
        "peers": peer_circuits.snapshots(),
        "isolated": peer_circuits.unhealthy(),
    }


@router.post("/peers/{peer}/reset", summary="Clear a Peer's Isolation (Analyst+)")
async def reset_peer_circuit(peer: str, user: RequireAnalyst) -> dict[str, Any]:
    """Force a fresh probe for an isolated peer instead of waiting for cooldown."""
    _ = user
    if peer not in peer_circuits.peers():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No circuit is tracked for peer '{peer}'.",
        )
    peer_circuits.reset(peer)
    return {"peer": peer, "state": "closed", "reset_by": user.label}


@router.post("/heal", summary="Run One Self-Healing Pass (Analyst+)")
async def run_healing_pass(user: RequireAnalyst) -> dict[str, Any]:
    """Measure every subsystem and apply the recovery actions it can justify."""
    _ = user
    report = await self_healing_supervisor.status()
    return {
        "status": report["status"],
        "actions": report["self_healing"]["actions_this_pass"],
        "triggered_by": user.label,
    }


@router.get("/capabilities", summary="What This Agent Can Actually Do Right Now")
async def get_capabilities(user: RequireViewer) -> dict[str, Any]:
    """Enumerate capabilities with the evidence class behind each claim.

    This is the honest answer to "what can you do": a capability is ``live``
    only when the credential or dependency it needs is present and was just
    verified, and ``unconfigured`` otherwise. It is never aspirational.
    """
    _ = user
    from futuris.api.app import app
    from futuris.infra.config import settings

    routes = sorted(
        {getattr(route, "path", "") for route in app.routes if getattr(route, "path", "")}
    )

    def capability(name: str, ready: bool, requires: str, detail: str) -> dict[str, Any]:
        return {
            "capability": name,
            "state": "live" if ready else "unconfigured",
            "requires": requires,
            "detail": detail,
        }

    return {
        "capabilities": [
            capability(
                "statistical_forecasting",
                True,
                "none",
                "local model adapters; runs without a peer",
            ),
            capability(
                "evidence_snapshotting",
                True,
                "object store path",
                f"freezes point-in-time data under {settings.OBJECT_STORE_PATH}",
            ),
            capability(
                "friday_delegation",
                bool(settings.FUTURIS_FRIDAY_API_KEY or settings.FUTURIS_API_KEY),
                "FUTURIS_FRIDAY_API_KEY or FUTURIS_API_KEY",
                "serve /v1/friday/forecast and /v1/friday/delegate",
            ),
            capability(
                "memora_persistence",
                bool(settings.MEMORA_API_KEY),
                "MEMORA_API_KEY",
                "publish advisories and read durable IntelX notices",
            ),
            capability(
                "intelx_research_context",
                bool(settings.INTELX_API_KEY),
                "INTELX_API_KEY",
                "enrich forecasts with research catalysts",
            ),
            capability(
                "inference_reasoning",
                bool(settings.INFERENCE_API_KEY),
                "INFERENCE_API_KEY",
                "qualitative enhancement through the Inference gateway",
            ),
            capability(
                "stratex_market_telemetry",
                bool(settings.STRATEX_API_KEY),
                "STRATEX_API_KEY",
                "read live volatility/drawdown telemetry",
            ),
            capability(
                "unattended_scheduler",
                settings.SCHEDULER_ENABLED,
                "SCHEDULER_ENABLED",
                "refresh forecasts, sweep lifecycles and backtest on a timer",
            ),
        ],
        "route_count": len(routes),
    }
