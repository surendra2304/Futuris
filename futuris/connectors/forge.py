"""Forge telemetry connector fetching build metrics, task rates, and resource utilization."""

from datetime import UTC, datetime
from typing import Any

import httpx

from futuris.connectors.base import BaseConnector, Observation
from futuris.core.enums import SignalClass
from futuris.infra.logging import get_logger

logger = get_logger("futuris.connectors.forge")


class ForgeConnector(BaseConnector):
    """Ingests Forge build telemetry: duration, verification rates, disk usage, and tokens."""

    def __init__(
        self,
        base_url: str = "http://forge-cluster.local",
        api_key: str | None = None,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "forge_default_secret_key"
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def fetch(self, start: datetime, end: datetime) -> list[Observation]:
        """Fetch Forge observations across [start, end]."""
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

        url = f"{self.base_url}/api/v1/telemetry/builds"
        logger.info(
            "forge_telemetry_fetch",
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

            metric_type = item.get("metric_type", "")
            if metric_type == "submission_rate":
                series_id = "forge:task:submission_rate"
                unit = "tasks_per_minute"
            elif metric_type == "build_duration":
                series_id = "forge:build:duration"
                unit = "seconds"
            elif metric_type == "verification_pass_rate":
                series_id = "forge:verification:pass_rate"
                unit = "ratio"
            elif metric_type == "disk_usage":
                series_id = "forge:workspace:disk_usage"
                unit = "GB"
            elif metric_type == "ai_universe_tokens":
                series_id = "forge:ai_universe:token_volume"
                unit = "tokens"
            else:
                series_id = item.get("series_id", f"forge:{metric_type}")
                unit = item.get("unit", "raw")

            observations.append(
                Observation(
                    observed_at=obs_dt,
                    source=item.get("source", "forge:builder"),
                    series_id=series_id,
                    value=float(item["value"]),
                    unit=unit,
                    tags={
                        "signal_class": SignalClass.TELEMETRY.value,
                        "template_id": item.get("template_id", "default_template"),
                        "complexity_score": item.get("complexity", 1.0),
                        **item.get("metadata", {}),
                    },
                )
            )

        logger.info("forge_fetch_completed", count=len(observations))
        return observations


# Standard Forge Forecast Targets
TARGET_BUILD_SUCCESS_PROBABILITY = "forge:build:success_probability_first_attempt"
TARGET_BUILD_DURATION = "forge:build:duration_seconds"
TARGET_CAPACITY_EXHAUSTION = "forge:capacity:exhaustion_24h"
TARGET_AI_UNIVERSE_COST = "forge:ai_universe:cost_usd_24h"
TARGET_TEMPLATE_SUCCESS = "forge:template:selection_success_rate"
