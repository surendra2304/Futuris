"""VyralX social trend diffusion and viral cascade telemetry connector."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx

from futuris.connectors.base import BaseConnector, Observation, warn_if_default_credential
from futuris.infra.config import settings
from futuris.infra.logging import get_logger
from futuris.infra.resilience import peer_circuits

logger = get_logger("futuris.connectors.vyralx")

VYRALX_DEFAULT_KEY = "vyralx_mesh_default_token"


class VyralXConnector(BaseConnector):
    """Fetches social diffusion and viral trend telemetry from VyralX (Agent 10)."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 6.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or settings.VYRALX_URL).rstrip("/")
        self.api_key = api_key or settings.VYRALX_API_KEY or VYRALX_DEFAULT_KEY
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        warn_if_default_credential("vyralx", self.api_key, VYRALX_DEFAULT_KEY, logger)

    async def fetch(
        self,
        start: datetime,
        end: datetime,
        series_id: str = "vyralx:trend:virality_index",
    ) -> list[Observation]:
        """Fetch viral trend and diffusion observations across time window [start, end]."""
        s_dt = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
        e_dt = end.replace(tzinfo=UTC) if end.tzinfo is None else end.astimezone(UTC)

        circuit = peer_circuits.get("vyralx")
        if not circuit.allow_request():
            logger.warning("vyralx_circuit_open_degrading", series_id=series_id)
            return self._generate_fallback_observations(s_dt, e_dt, series_id)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "X-Caller-Agent": "futuris",
        }
        params = {
            "start": s_dt.isoformat(),
            "end": e_dt.isoformat(),
            "series_id": series_id,
        }
        url = f"{self.base_url}/v1/trends/telemetry"

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self.transport
            ) as client:
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code == 200:
                    circuit.record_success()
                    data = resp.json()
                    return self._parse_observations(data, series_id)

                logger.warning(
                    "vyralx_telemetry_non_200", status=resp.status_code, body=resp.text[:200]
                )
                circuit.record_failure()
        except Exception as exc:
            circuit.record_failure()
            logger.warning("vyralx_telemetry_fetch_failed", error=str(exc))

        return self._generate_fallback_observations(s_dt, e_dt, series_id)

    async def fetch_viral_cascades(
        self,
        topic: str,
        horizon_hours: int = 24,
    ) -> dict[str, Any]:
        """Fetch expected viral cascade progression and reproductive ratio (R0/branching)."""
        circuit = peer_circuits.get("vyralx")
        if not circuit.allow_request():
            return {
                "topic": topic,
                "status": "degraded_circuit_open",
                "branching_ratio": 1.0,
                "projected_peak_hours": 12.0,
                "diffusion_velocity": 0.0,
            }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        url = f"{self.base_url}/v1/trends/cascades/{topic}"
        params = {"horizon_hours": horizon_hours}

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self.transport
            ) as client:
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code == 200:
                    circuit.record_success()
                    return resp.json()
                circuit.record_failure()
        except Exception as exc:
            circuit.record_failure()
            logger.warning("vyralx_cascades_fetch_failed", topic=topic, error=str(exc))

        return {
            "topic": topic,
            "status": "fallback",
            "branching_ratio": 1.05,
            "projected_peak_hours": float(horizon_hours // 2),
            "diffusion_velocity": 50.0,
        }

    async def fetch_sentiment_inflections(self, topic: str) -> list[dict[str, Any]]:
        """Fetch real-time social sentiment inflection points and trend acceleration."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        url = f"{self.base_url}/v1/trends/sentiment/{topic}"

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self.transport
            ) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.json().get("inflections", [])
        except Exception as exc:
            logger.warning("vyralx_sentiment_fetch_failed", topic=topic, error=str(exc))

        return []

    def _parse_observations(
        self,
        payload: dict[str, Any] | list[dict[str, Any]],
        series_id: str,
    ) -> list[Observation]:
        items = payload if isinstance(payload, list) else payload.get("data", [])
        observations: list[Observation] = []
        for item in items:
            try:
                obs_dt = datetime.fromisoformat(item["observed_at"])
                if obs_dt.tzinfo is None:
                    obs_dt = obs_dt.replace(tzinfo=UTC)
                observations.append(
                    Observation(
                        observation_id=uuid4(),
                        observed_at=obs_dt,
                        source="vyralx:social_mesh",
                        series_id=series_id,
                        value=float(item["value"]),
                        unit=item.get("unit", "virality_score"),
                        tags=item.get("tags", {}),
                    )
                )
            except Exception as parse_err:
                logger.debug("vyralx_parse_row_skipped", error=str(parse_err))
        return sorted(observations, key=lambda o: o.observed_at)

    def _generate_fallback_observations(
        self,
        start: datetime,
        end: datetime,
        series_id: str,
    ) -> list[Observation]:
        """Generate smooth synthetic baseline when VyralX connection is degraded."""
        logger.info("generating_vyralx_fallback_telemetry", series_id=series_id)
        current = start
        step = timedelta(minutes=15)
        results: list[Observation] = []
        t_idx = 0

        while current <= end:
            # Deterministic harmonic trend with baseline growth
            base_score = 50.0 + 15.0 * math.sin(t_idx * 0.15) + (t_idx * 0.2)
            results.append(
                Observation(
                    observation_id=uuid4(),
                    observed_at=current,
                    source="vyralx:fallback",
                    series_id=series_id,
                    value=round(base_score, 2),
                    unit="virality_score",
                    tags={"degraded": "circuit_open_or_offline"},
                )
            )
            current += step
            t_idx += 1

        return results
