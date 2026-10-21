"""TradingBot telemetry connector fetching trading performance and market metrics."""

from datetime import UTC, datetime
from typing import Any

import httpx

from futuris.connectors.base import BaseConnector, Observation, warn_if_default_credential
from futuris.core.enums import SignalClass
from futuris.infra.logging import get_logger

logger = get_logger("futuris.connectors.trading_bot")

#: Public placeholders, kept only so existing constructions keep working; using
#: them is logged loudly (see warn_if_default_credential).
TRADING_BOT_DEFAULT_KEY = "trading_bot_default_key"
TRADING_BOT_DEFAULT_READ_KEY = "read_key_default_secret_123"


class TradingBotConnector(BaseConnector):
    """Ingests trading telemetry including equity, volatility, drawdowns, and advisory outcomes."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        from futuris.infra.config import settings

        resolved_url = (
            base_url
            or getattr(settings, "STRATEX_URL", None)
            or "http://trading-bot.local"
        )
        self.base_url = resolved_url.rstrip("/")
        self.api_key = (
            api_key
            or getattr(settings, "STRATEX_API_KEY", None)
            or TRADING_BOT_DEFAULT_KEY
        )
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        if self.api_key == TRADING_BOT_DEFAULT_KEY:
            warn_if_default_credential("trading_bot", self.api_key, TRADING_BOT_DEFAULT_KEY, logger)

    async def fetch(self, start: datetime, end: datetime) -> list[Observation]:
        """Fetch trading observations across [start, end]."""
        s_dt = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
        e_dt = end.replace(tzinfo=UTC) if end.tzinfo is None else end.astimezone(UTC)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-API-Key": self.api_key
            if self.api_key != TRADING_BOT_DEFAULT_KEY
            else TRADING_BOT_DEFAULT_READ_KEY,
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

        data: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=self.timeout_seconds
            ) as client:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                elif resp.status_code == 404:
                    # A status snapshot may contain current values, but never
                    # manufacture a time series from a single snapshot.
                    status_url = f"{self.base_url}/api/v1/status"
                    resp_status = await client.get(status_url, headers=headers)
                    if resp_status.status_code == 200:
                        status_json = resp_status.json().get("data", {})
                        observed_at = status_json.get("updated_at") or status_json.get("timestamp")
                        if observed_at:
                            for key, metric_type in (
                                ("equity", "equity"),
                                ("max_drawdown_pct", "drawdown"),
                                ("volatility", "volatility"),
                            ):
                                value = status_json.get(key)
                                if isinstance(value, (int, float)) and not isinstance(value, bool):
                                    data.append(
                                        {
                                            "timestamp": observed_at,
                                            "metric_type": metric_type,
                                            "value": value,
                                            "source": "stratex:status_snapshot",
                                        }
                                    )
        except Exception as exc:
            logger.warning("trading_telemetry_unavailable_no_fallback", error=type(exc).__name__)

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

    async def fetch_ticker_series(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[Observation]:
        """Fetch historical price and volatility observations for a specific market ticker.

        Supports target assets like BTCUSDT, ETHUSDT, SOLUSDT from Stratex.
        """
        s_dt = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
        e_dt = end.replace(tzinfo=UTC) if end.tzinfo is None else end.astimezone(UTC)
        sym_clean = symbol.upper().replace("-", "").replace("/", "")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-API-Key": self.api_key
            if self.api_key != TRADING_BOT_DEFAULT_KEY
            else TRADING_BOT_DEFAULT_READ_KEY,
            "Accept": "application/json",
        }
        params = {
            "symbol": sym_clean,
            "start": s_dt.isoformat(),
            "end": e_dt.isoformat(),
        }

        url = f"{self.base_url}/api/v1/telemetry/market/{sym_clean}"
        data: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=self.timeout_seconds
            ) as client:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code == 200:
                    res_json = resp.json()
                    data = res_json if isinstance(res_json, list) else res_json.get("data", [])
                elif resp.status_code == 404:
                    fallback_url = f"{self.base_url}/api/v1/telemetry/trading"
                    resp2 = await client.get(fallback_url, params=params, headers=headers)
                    if resp2.status_code == 200:
                        res_json2 = resp2.json()
                        data = (
                            res_json2
                            if isinstance(res_json2, list)
                            else res_json2.get("data", [])
                        )
        except Exception as exc:
            logger.warning(
                "ticker_telemetry_unavailable",
                symbol=sym_clean,
                error=f"{type(exc).__name__}: {exc}",
            )

        observations: list[Observation] = []
        for item in data:
            raw_ts = item.get("timestamp") or item.get("observed_at")
            if not raw_ts:
                continue
            raw_dt = datetime.fromisoformat(str(raw_ts))
            obs_dt = raw_dt.replace(tzinfo=UTC) if raw_dt.tzinfo is None else raw_dt.astimezone(UTC)
            val = float(item.get("value") or item.get("price") or 0.0)
            m_type = item.get("metric_type", "price")
            observations.append(
                Observation(
                    observed_at=obs_dt,
                    source=f"stratex:{sym_clean}",
                    series_id=f"trading:{sym_clean}:{m_type}",
                    value=val,
                    unit=item.get("unit", "USD"),
                    tags={
                        "signal_class": SignalClass.TELEMETRY.value,
                        "symbol": sym_clean,
                        **item.get("metadata", {}),
                    },
                )
            )

        logger.info("ticker_series_fetch_completed", symbol=sym_clean, count=len(observations))
        return observations


# Standard Trading Forecast Targets
TARGET_VOLATILITY_SPIKE = "trading:btc:volatility_spike_24h"
TARGET_DRAWDOWN_RISK = "trading:portfolio:drawdown_exceedance_7d"
TARGET_STRATEGY_DEGRADATION = "trading:strategy:winrate_degradation_14d"
TARGET_CAPITAL_UTILIZATION = "trading:portfolio:capital_utilization_24h"
TARGET_REGIME_TRANSITION = "trading:market:regime_transition_24h"

# Primary Market Target Series
TARGET_BTC_PRICE_VARIANCE = "trading:BTCUSDT:price_variance_24h"
TARGET_ETH_PRICE_VARIANCE = "trading:ETHUSDT:price_variance_24h"
TARGET_SOL_PRICE_VARIANCE = "trading:SOLUSDT:price_variance_24h"
