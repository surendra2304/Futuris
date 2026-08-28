"""TradingBot telemetry connector fetching trading performance and market metrics."""

from datetime import UTC, datetime
from typing import Any

import httpx

from futuris.connectors.base import BaseConnector, Observation
from futuris.core.enums import SignalClass
from futuris.infra.logging import get_logger

logger = get_logger("futuris.connectors.trading_bot")


class TradingBotConnector(BaseConnector):
    """Ingests trading telemetry including equity, volatility, drawdowns, and advisory outcomes."""

    def __init__(
        self,
        base_url: str = "http://trading-bot.local",
        api_key: str | None = None,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "trading_bot_default_key"
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def fetch(self, start: datetime, end: datetime) -> list[Observation]:
        """Fetch trading observations across [start, end]."""
        s_dt = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
        e_dt = end.replace(tzinfo=UTC) if end.tzinfo is None else end.astimezone(UTC)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        params = {
            "start": s_dt.isoformat(),
            "end": e_dt.isoformat(),
        }

        url = f"{self.base_url}/api/v1/telemetry/trading"
        logger.info(
            "trading_telemetry_fetch",
            url=url,
            start=s_dt.isoformat(),
            end=e_dt.isoformat(),
        )

        async with httpx.AsyncClient(
            transport=self.transport, timeout=self.timeout_seconds
        ) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data: list[dict[str, Any]] = resp.json()

        observations: list[Observation] = []
        for item in data:
            raw_dt = datetime.fromisoformat(item["timestamp"])
            obs_dt = raw_dt.replace(tzinfo=UTC) if raw_dt.tzinfo is None else raw_dt.astimezone(UTC)

            # Map raw series types to normalized series_id constants
            raw_type = item.get("metric_type", "")
            if raw_type == "equity":
                series_id = "trading:equity"
                unit = "USD"
            elif raw_type == "volatility":
                symbol = item.get("symbol", "btc").lower()
                series_id = f"trading:volatility:{symbol}"
                unit = "volatility_idx"
            elif raw_type == "drawdown":
                series_id = "trading:drawdown"
                unit = "percentage"
            elif raw_type == "position_exposure":
                series_id = "trading:position_exposure"
                unit = "ratio"
            elif raw_type == "advisory_accuracy":
                series_id = "trading:advisory_accuracy"
                unit = "win_rate"
            else:
                series_id = item.get("series_id", f"trading:{raw_type}")
                unit = item.get("unit", "raw")

            observations.append(
                Observation(
                    observed_at=obs_dt,
                    source=item.get("source", "trading_bot:core"),
                    series_id=series_id,
                    value=float(item["value"]),
                    unit=unit,
                    tags={
                        "signal_class": SignalClass.TELEMETRY.value,
                        "strategy": item.get("strategy", "momentum_v1"),
                        "market_regime": item.get("regime", "trending"),
                        **item.get("metadata", {}),
                    },
                )
            )

        logger.info("trading_fetch_completed", count=len(observations))
        return observations


# Standard Trading Forecast Targets
TARGET_VOLATILITY_SPIKE = "trading:btc:volatility_spike_24h"
TARGET_DRAWDOWN_RISK = "trading:portfolio:drawdown_exceedance_7d"
TARGET_STRATEGY_DEGRADATION = "trading:strategy:winrate_degradation_14d"
TARGET_CAPITAL_UTILIZATION = "trading:portfolio:capital_utilization_24h"
TARGET_REGIME_TRANSITION = "trading:market:regime_transition_24h"
