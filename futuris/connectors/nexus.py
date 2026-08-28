"""NEXUS telemetry connector fetching operational observations over authenticated HTTP."""

from datetime import UTC, datetime
from typing import Any

import httpx

from futuris.connectors.base import BaseConnector, Observation
from futuris.infra.logging import get_logger

logger = get_logger("futuris.connectors.nexus")


class NexusConnector(BaseConnector):
    """Fetches telemetry series observations from a NEXUS data broker endpoint."""

    def __init__(
        self,
        base_url: str = "http://nexus-service.local",
        api_key: str | None = None,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or "nexus_default_token"
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def fetch(self, start: datetime, end: datetime) -> list[Observation]:
        """Fetch observations across time interval [start, end] from NEXUS."""
        s_dt = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
        e_dt = end.replace(tzinfo=UTC) if end.tzinfo is None else end.astimezone(UTC)
        start_iso = s_dt.isoformat()
        end_iso = e_dt.isoformat()

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        params = {
            "start": start_iso,
            "end": end_iso,
        }

        url = f"{self.base_url}/api/v1/telemetry"
        logger.info("nexus_fetch_request", url=url, start=start_iso, end=end_iso)

        async with httpx.AsyncClient(
            transport=self.transport, timeout=self.timeout_seconds
        ) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data: list[dict[str, Any]] = resp.json()

        observations: list[Observation] = []
        for item in data:
            raw_dt = datetime.fromisoformat(item["observed_at"])
            obs_dt = raw_dt.replace(tzinfo=UTC) if raw_dt.tzinfo is None else raw_dt.astimezone(UTC)

            observations.append(
                Observation(
                    observed_at=obs_dt,
                    source=item.get("source", "nexus:telemetry"),
                    series_id=item["series_id"],
                    value=float(item["value"]),
                    unit=item.get("unit", "rpm"),
                    tags=item.get("tags", {}),
                )
            )

        logger.info("nexus_fetch_completed", count=len(observations))
        return observations
