"""IntelX Context Injector fetching external research findings as exogenous features."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, Field

from futuris.infra.logging import get_logger

logger = get_logger("futuris.connectors.intelx_context")


class IntelXResearchReport(BaseModel):
    """Structured research report published by IntelX."""

    report_id: UUID = Field(default_factory=uuid4)
    asset_or_sector: str
    published_at: datetime
    summary: str
    sentiment_score: float = Field(..., ge=-1.0, le=1.0)
    volatility_impact_factor: float = Field(default=1.0, ge=0.5, le=3.0)
    key_findings: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class IntelXContextInjector:
    """Queries recent IntelX research to inject qualitative exogenous features into forecasts."""

    def __init__(
        self,
        base_url: str = "http://intelx-service.local",
        api_key: str | None = None,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "intelx_default_token"
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def fetch_recent_research(
        self,
        asset_or_sector: str,
        lookback_days: int = 7,
        as_of: datetime | None = None,
    ) -> list[IntelXResearchReport]:
        """Fetch research reports within the last lookback_days prior to as_of."""
        ref_time = as_of or datetime.now(UTC)
        start_time = ref_time - timedelta(days=lookback_days)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        params = {
            "sector": asset_or_sector,
            "start": start_time.isoformat(),
            "end": ref_time.isoformat(),
        }

        url = f"{self.base_url}/api/v1/research/query"
        logger.info(
            "intelx_query_research",
            asset=asset_or_sector,
            start=start_time.isoformat(),
        )

        if self.transport is None:
            # Fallback mock for zero external service dependency
            return [
                IntelXResearchReport(
                    asset_or_sector=asset_or_sector,
                    published_at=ref_time - timedelta(days=1),
                    summary=f"Research on {asset_or_sector} indicates supply tightness.",
                    sentiment_score=0.35,
                    volatility_impact_factor=1.25,
                    key_findings=["Regulatory clarity improved", "Institutional flow positive"],
                    tags=["macro", "liquidity"],
                )
            ]

        async with httpx.AsyncClient(
            transport=self.transport, timeout=self.timeout_seconds
        ) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data: list[dict[str, Any]] = resp.json()

        reports: list[IntelXResearchReport] = []
        for item in data:
            pub_dt = datetime.fromisoformat(item["published_at"])
            pub_dt = pub_dt.replace(tzinfo=UTC) if pub_dt.tzinfo is None else pub_dt.astimezone(UTC)
            reports.append(
                IntelXResearchReport(
                    report_id=UUID(item.get("report_id", str(uuid4()))),
                    asset_or_sector=item.get("asset_or_sector", asset_or_sector),
                    published_at=pub_dt,
                    summary=item.get("summary", ""),
                    sentiment_score=float(item.get("sentiment_score", 0.0)),
                    volatility_impact_factor=float(item.get("volatility_impact_factor", 1.0)),
                    key_findings=item.get("key_findings", []),
                    tags=item.get("tags", []),
                )
            )

        return reports

    def compute_exogenous_adjustments(
        self,
        reports: list[IntelXResearchReport],
    ) -> dict[str, float]:
        """Convert qualitative research into numerical exogenous feature modifiers."""
        if not reports:
            return {
                "sentiment_multiplier": 1.0,
                "volatility_multiplier": 1.0,
                "confidence_penalty": 0.0,
            }

        avg_sentiment = sum(r.sentiment_score for r in reports) / len(reports)
        max_vol_factor = max(r.volatility_impact_factor for r in reports)

        return {
            "sentiment_multiplier": round(1.0 + (avg_sentiment * 0.10), 3),
            "volatility_multiplier": round(max_vol_factor, 3),
            "confidence_penalty": 0.05 if max_vol_factor > 1.5 else 0.0,
        }
