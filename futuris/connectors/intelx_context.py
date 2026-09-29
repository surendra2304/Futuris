"""IntelX Context Injector fetching external research findings as exogenous features."""

from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select

from futuris.infra.logging import get_logger
from futuris.storage.db import async_session_factory
from futuris.storage.models import IntelXNoticeModel

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
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        logger.info(
            "intelx_query_research",
            target=asset_or_sector,
            base_url=self.base_url,
            start=start_time.isoformat(),
        )

        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=self.timeout_seconds
            ) as client:
                # 1. First attempt dedicated Futuris context endpoint on IntelX with appropriate domain
                is_market = any(k in asset_or_sector.lower() for k in ["btc", "eth", "sol", "crypto", "trading", "market", "usdt"])
                domain = "market" if is_market else "general"
                context_url = f"{self.base_url}/api/v1/futuris/context"
                payload = {
                    "forecast_target": asset_or_sector,
                    "horizon": "24h",
                    "requesting_context": {
                        "domain": domain,
                        "lookback_days": lookback_days,
                    },
                }

                try:
                    resp = await client.post(context_url, json=payload, headers=headers)
                    if resp.status_code == 200:
                        raw_data = resp.json()
                    elif resp.status_code in (404, 405):
                        # Fallback to query endpoint
                        query_url = f"{self.base_url}/api/v1/research/query"
                        params = {
                            "sector": asset_or_sector,
                            "start": start_time.isoformat(),
                            "end": ref_time.isoformat(),
                        }
                        resp2 = await client.get(query_url, params=params, headers=headers)
                        resp2.raise_for_status()
                        raw_data = resp2.json()
                    else:
                        resp.raise_for_status()
                        raw_data = resp.json()
                except (httpx.HTTPStatusError, httpx.RequestError):
                    # Try query endpoint if post failed
                    query_url = f"{self.base_url}/api/v1/research/query"
                    params = {
                        "sector": asset_or_sector,
                        "start": start_time.isoformat(),
                        "end": ref_time.isoformat(),
                    }
                    resp2 = await client.get(query_url, params=params, headers=headers)
                    resp2.raise_for_status()
                    raw_data = resp2.json()

            # Parse results
            reports: list[IntelXResearchReport] = []

            # Handle list format (e.g. from mock or query endpoint)
            if isinstance(raw_data, list):
                for item in raw_data:
                    if not isinstance(item, dict):
                        continue
                    report_id = str(item.get("report_id", "")).strip()
                    tags = item.get("tags", [])
                    summary = item.get("summary")
                    published = item.get("published_at")
                    target = item.get("asset_or_sector")
                    # IntelX's query API can return a synthetic baseline when
                    # its database has no research runs. Never treat that
                    # baseline as new market evidence.
                    if (
                        report_id == "intelx-baseline-report"
                        or "baseline" in {str(tag).lower() for tag in tags if isinstance(tag, str)}
                        or not isinstance(summary, str)
                        or not summary.strip()
                        or not isinstance(published, str)
                        or not isinstance(target, str)
                        or not target.strip()
                    ):
                        continue
                    try:
                        pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                        pub_dt = (
                            pub_dt.replace(tzinfo=UTC)
                            if pub_dt.tzinfo is None
                            else pub_dt.astimezone(UTC)
                        )
                        if pub_dt < start_time or pub_dt > ref_time:
                            continue
                        stable_id = UUID(report_id) if report_id else uuid5(
                            NAMESPACE_URL,
                            f"intelx:{target}:{pub_dt.isoformat()}:{summary.strip()}",
                        )
                        reports.append(
                            IntelXResearchReport(
                                report_id=stable_id,
                                asset_or_sector=target.strip(),
                                published_at=pub_dt,
                                summary=summary.strip(),
                                sentiment_score=float(item.get("sentiment_score", 0.0)),
                                volatility_impact_factor=float(
                                    item.get("volatility_impact_factor", 1.0)
                                ),
                                key_findings=item.get("key_findings", []),
                                tags=tags,
                            )
                        )
                    except (TypeError, ValueError):
                        logger.warning("intelx_report_invalid_skipped")
                return reports

            # Handle dict format (ForecastContextResponse)
            if isinstance(raw_data, dict):
                findings = raw_data.get("research_findings", [])
                signals = raw_data.get("exogenous_signals", [])

                if findings or signals:
                    findings_texts = [f.get("finding", "") for f in findings if "finding" in f]
                    sentiment = 0.0
                    vol_factor = 1.0
                    for s in signals:
                        dir_str = s.get("direction", "neutral")
                        mag = float(s.get("magnitude", 0.0) or 0.0)
                        if dir_str == "positive":
                            sentiment += 0.25
                        elif dir_str == "negative":
                            sentiment -= 0.25
                        elif dir_str == "volatile":
                            vol_factor = max(vol_factor, 1.35)

                    sentiment = max(-1.0, min(1.0, sentiment))
                    reports.append(
                        IntelXResearchReport(
                            report_id=uuid4(),
                            asset_or_sector=asset_or_sector,
                            published_at=ref_time - timedelta(days=1),
                            summary=f"IntelX context for {asset_or_sector}: {len(findings)} findings, {len(signals)} signals.",
                            sentiment_score=sentiment,
                            volatility_impact_factor=vol_factor,
                            key_findings=findings_texts,
                            tags=["intelx", "live_context"],
                        )
                    )
                    return reports

        except Exception as exc:
            logger.warning(
                "intelx_fetch_failed_no_evidence_returned",
                target=asset_or_sector,
                error=type(exc).__name__,
            )
        # Missing or empty upstream data is not market/news evidence. Callers can
        # continue with their primary data and receive neutral IntelX modifiers.
        return []

    async def fetch_durable_notice_context(
        self,
        asset_or_sector: str,
        lookback_days: int = 7,
        as_of: datetime | None = None,
    ) -> list[IntelXResearchReport]:
        """Load genuine IntelX inbox notices relevant to an explicitly tagged market.

        News without an explicit asset or market/crypto classification is kept
        in the inbox, but is not injected into an unrelated market forecast.
        Missing sentiment/volatility measurements remain neutral; no values are
        inferred from prose.
        """
        ref_time = as_of or datetime.now(UTC)
        start_time = ref_time - timedelta(days=lookback_days)
        requested = asset_or_sector.strip().upper()
        async with async_session_factory() as session:
            result = await session.execute(
                select(IntelXNoticeModel)
                .where(IntelXNoticeModel.received_at >= start_time)
                .order_by(IntelXNoticeModel.received_at.desc())
                .limit(200)
            )
            notices = result.scalars().all()

        reports: list[IntelXResearchReport] = []
        for notice in notices:
            payload = notice.payload if isinstance(notice.payload, dict) else {}
            if not self._notice_matches_market(payload, requested):
                continue
            summary = str(
                payload.get("finding_summary")
                or payload.get("summary")
                or payload.get("headline")
                or ""
            ).strip()
            if not summary:
                continue
            try:
                sentiment = payload.get("sentiment_score", 0.0)
                if isinstance(sentiment, bool) or not isinstance(sentiment, (int, float)):
                    sentiment = 0.0
                sentiment = float(sentiment)
                if not isfinite(sentiment) or not -1.0 <= sentiment <= 1.0:
                    sentiment = 0.0
                volatility = payload.get("volatility_impact_factor", 1.0)
                if isinstance(volatility, bool) or not isinstance(volatility, (int, float)):
                    volatility = 1.0
                volatility = float(volatility)
                if not isfinite(volatility) or not 0.5 <= volatility <= 3.0:
                    volatility = 1.0
                published = payload.get("published_at") or payload.get("timestamp")
                published_at = (
                    datetime.fromisoformat(published.replace("Z", "+00:00"))
                    if isinstance(published, str)
                    else notice.received_at
                )
                published_at = (
                    published_at.replace(tzinfo=UTC)
                    if published_at.tzinfo is None
                    else published_at.astimezone(UTC)
                )
                reports.append(
                    IntelXResearchReport(
                        report_id=uuid5(NAMESPACE_URL, f"futuris-intelx:{notice.event_id}"),
                        asset_or_sector=asset_or_sector,
                        published_at=published_at,
                        summary=summary[:4000],
                        sentiment_score=sentiment,
                        volatility_impact_factor=volatility,
                        key_findings=[summary[:1000]],
                        tags=["intelx", "memora_event_inbox"],
                    )
                )
            except (TypeError, ValueError):
                logger.warning("intelx_notice_context_invalid", event_id=notice.event_id)
        return reports

    @staticmethod
    def _notice_matches_market(payload: dict[str, Any], requested: str) -> bool:
        """Require explicit target metadata or a market/crypto classification."""
        aliases = {
            "BTCUSDT": {"BTCUSDT", "BTC", "BITCOIN"},
            "ETHUSDT": {"ETHUSDT", "ETH", "ETHEREUM"},
            "SOLUSDT": {"SOLUSDT", "SOL", "SOLANA"},
        }
        requested_aliases = aliases.get(requested, {requested})
        explicit_values: list[str] = []
        for key in ("symbol", "asset", "asset_or_sector"):
            value = payload.get(key)
            if isinstance(value, str):
                explicit_values.append(value.upper())
        for key in ("symbols", "assets", "target_assets", "recommended_forecast_targets"):
            values = payload.get(key)
            if isinstance(values, list):
                explicit_values.extend(value.upper() for value in values if isinstance(value, str))
        for value in explicit_values:
            if value in requested_aliases or value.endswith(f":{requested}"):
                return True
        relevance = payload.get("relevance")
        domain = relevance.get("domain", "") if isinstance(relevance, dict) else payload.get("domain", "")
        category = relevance.get("category", "") if isinstance(relevance, dict) else payload.get("category", "")
        market_labels = {str(domain).lower(), str(category).lower()}
        return bool(market_labels & {"market", "crypto", "cryptocurrency", "digital_assets"})

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
