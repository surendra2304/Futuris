"""Inbound webhook router receiving research catalysts and external triggers."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from pydantic import BaseModel, Field

from futuris.api.deps import get_forecast_repo
from futuris.api.routers.market import _generate_market_prediction
from futuris.infra.logging import get_logger
from futuris.storage.repositories import ForecastRepository

logger = get_logger("futuris.api.webhooks")

router = APIRouter(tags=["Webhooks and Research Catalysts"])


class ResearchFindingEventData(BaseModel):
    run_id: str = Field(..., description="IntelX research run ID")
    finding_summary: str = Field(..., description="Key catalyst or finding text")
    category: str = Field(default="market_moving", description="market_moving | regulatory_change | emerging_threat")
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    domain: str = Field(default="market")
    recommended_forecast_targets: list[str] = Field(default_factory=list)
    timestamp: str | None = None


class IntelXResearchWebhookPayload(BaseModel):
    event: str = Field(default="research_finding_relevant")
    data: ResearchFindingEventData


@router.post(
    "/webhooks/research-finding-relevant",
    status_code=status.HTTP_200_OK,
    summary="IntelX Catalyst Webhook Handler",
)
async def handle_intelx_research_catalyst(
    payload: IntelXResearchWebhookPayload,
    background_tasks: BackgroundTasks,
    forecast_repo: ForecastRepository = Depends(get_forecast_repo),
) -> dict[str, Any]:
    """Receives significant research catalysts from IntelX and triggers automatic re-forecasting."""
    data = payload.data
    logger.info(
        "intelx_catalyst_webhook_received",
        category=data.category,
        domain=data.domain,
        summary=data.finding_summary[:60],
        targets=data.recommended_forecast_targets,
    )

    # Determine asset symbol
    symbol = "BTCUSDT"
    summary_lower = data.finding_summary.lower()
    if "eth" in summary_lower:
        symbol = "ETHUSDT"
    elif "sol" in summary_lower:
        symbol = "SOLUSDT"

    # Trigger async re-forecast with updated market catalyst
    async def _async_reforecast():
        try:
            logger.info("intelx_catalyst_triggering_market_reforecast", symbol=symbol)
            await _generate_market_prediction(
                symbol=symbol,
                horizon_hours=24,
                include_intelx=True,
                include_inference=True,
                forecast_repo=forecast_repo,
            )
        except Exception as exc:
            logger.warning("catalyst_reforecast_failed", error=str(exc))

    background_tasks.add_task(_async_reforecast)

    return {
        "status": "acknowledged",
        "event": payload.event,
        "category": data.category,
        "affected_symbol": symbol,
        "recalibration_scheduled": True,
        "received_at": datetime.now(UTC).isoformat(),
    }
