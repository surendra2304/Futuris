"""Typed client SDK for consuming FUTURIS predictive intelligence from FRIDAY."""

from typing import Any
from uuid import UUID

import httpx

from futuris.api.routers.forecasts import ForecastResponse
from futuris.core.enums import ConfidenceLevel, ForecastEventType
from futuris.scenarios.engine import ScenarioComparison
from futuris.scenarios.spec import ScenarioSpec


class FridayClient:
    """Typed client SDK for FRIDAY autonomous orchestrator communicating with FUTURIS.

    Usage Pattern ('Simulate Before Act'):
    ```python
    client = FridayClient(base_url="http://127.0.0.1:8000")

    # 1. Request baseline forecast
    forecast = await client.request_forecast(
        target="service:checkout:capacity_exceedance_24h",
        horizon="24h",
    )

    # 2. Before executing scaling/shedding, simulate counterfactual stress scenario
    stress_spec = ScenarioSpec.stress_spec(demand_multiplier=1.4, capacity_override=3200)
    comparison = await client.compare_scenarios(
        forecast_id=forecast.forecast_id,
        scenarios=[stress_spec],
    )

    # 3. If divergence exceeds safety thresholds, seek human approval prior to action
    if comparison.divergence_ranking[0][1] > 20.0:
        print("High divergence detected: Action requires human approval gate.")
    ```
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    async def request_forecast(
        self,
        target: str,
        horizon: str = "24h",
        context: dict[str, Any] | None = None,
        required_confidence: ConfidenceLevel | None = None,
    ) -> ForecastResponse:
        """Request a fresh predictive forecast for target metric."""
        payload: dict[str, Any] = {
            "target": target,
            "horizon": horizon,
            "context": context or {},
        }
        if required_confidence:
            payload["required_confidence"] = required_confidence.value

        url = f"{self.base_url}/v1/forecasts"
        async with httpx.AsyncClient(
            transport=self.transport, base_url=self.base_url
        ) as client:
            resp = await client.post(url, json=payload, headers=self._headers())
            resp.raise_for_status()
            return ForecastResponse.model_validate(resp.json())

    async def compare_scenarios(
        self,
        forecast_id: UUID,
        scenarios: list[ScenarioSpec],
        use_monte_carlo: bool = False,
    ) -> ScenarioComparison:
        """Simulate and compare multiple scenario specifications against parent forecast."""
        payload = {
            "scenarios": [s.model_dump(mode="json") for s in scenarios],
            "use_monte_carlo": use_monte_carlo,
        }
        url = f"{self.base_url}/v1/forecasts/{forecast_id}/scenarios/compare"
        async with httpx.AsyncClient(
            transport=self.transport, base_url=self.base_url
        ) as client:
            resp = await client.post(url, json=payload, headers=self._headers())
            resp.raise_for_status()
            return ScenarioComparison.model_validate(resp.json())

    async def subscribe_webhook(
        self,
        url: str,
        event_types: list[ForecastEventType] | None = None,
    ) -> dict[str, Any]:
        """Subscribe webhook endpoint to receive HMAC-signed forecast events."""
        types = event_types or [ForecastEventType.FORECAST_THRESHOLD_CROSSED]
        payload = {
            "url": url,
            "event_types": [e.value for e in types],
        }
        sub_url = f"{self.base_url}/v1/webhooks"
        async with httpx.AsyncClient(
            transport=self.transport, base_url=self.base_url
        ) as client:
            resp = await client.post(sub_url, json=payload, headers=self._headers())
            resp.raise_for_status()
            return resp.json()
