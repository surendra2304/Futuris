"""Cross-agent predictive signaling and risk regime dispatching across the FRIDAY Universe mesh."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from futuris.infra.config import settings
from futuris.infra.logging import get_logger
from futuris.infra.metrics import PEER_CALL_TOTAL
from futuris.infra.resilience import peer_circuits

logger = get_logger("futuris.ecosystem.signals")


class SignalDispatcher:
    """Dispatches calibrated forecast signals and risk regimes to FRIDAY and Stratex."""

    def __init__(
        self,
        timeout_seconds: float = 4.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.timeout = timeout_seconds
        self.transport = transport

    async def notify_friday_forecast(
        self,
        forecast_payload: dict[str, Any],
    ) -> bool:
        """Push calibrated predictive signal to FRIDAY central orchestrator.

        Target endpoint: POST /v1/forecasts/broadcast
        """
        breaker = peer_circuits.for_peer("FRIDAY")
        if not breaker.allow_request():
            breaker.total_short_circuits += 1
            PEER_CALL_TOTAL.labels(peer="FRIDAY", outcome="short_circuited").inc()
            logger.warning(
                "friday_signal_broadcast_short_circuited",
                circuit_state=breaker.state.value,
            )
            return False

        friday_url = getattr(settings, "FRIDAY_URL", "http://friday:10000").rstrip("/")
        url = f"{friday_url}/v1/forecasts/broadcast"
        friday_key = (
            getattr(settings, "FRIDAY_API_KEY", "")
            or getattr(settings, "FUTURIS_FRIDAY_API_KEY", "")
            or getattr(settings, "SECRET_KEY", "")
        )

        headers = {
            "Authorization": f"Bearer {friday_key}",
            "X-Caller-Agent": "futuris",
            "Content-Type": "application/json",
        }

        body = {
            "broadcast_id": str(uuid4()),
            "source_agent": "futuris",
            "timestamp": datetime.now(UTC).isoformat(),
            "signal_type": "forecast_advisory",
            "advisory_notice": "prediction_is_not_authorization",
            "forecast": forecast_payload,
        }

        breaker.total_calls += 1
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                resp = await client.post(url, json=body, headers=headers)
                if resp.status_code in {200, 201, 202}:
                    breaker.record_success()
                    PEER_CALL_TOTAL.labels(peer="FRIDAY", outcome="ok").inc()
                    logger.info("friday_forecast_broadcast_success", status=resp.status_code)
                    return True
                if resp.status_code >= 500:
                    breaker.record_failure(f"http_{resp.status_code}")
                    PEER_CALL_TOTAL.labels(peer="FRIDAY", outcome="failed").inc()
                else:
                    breaker.record_success()
                    PEER_CALL_TOTAL.labels(peer="FRIDAY", outcome="ok").inc()
                logger.warning("friday_forecast_broadcast_non_200", status=resp.status_code)
                return False
        except Exception as exc:
            breaker.record_failure(f"{type(exc).__name__}: {exc}")
            PEER_CALL_TOTAL.labels(peer="FRIDAY", outcome="failed").inc()
            logger.warning("friday_forecast_broadcast_failed", error=str(exc))
            return False

    async def notify_stratex_risk_regime(
        self,
        target: str,
        risk_report: dict[str, Any],
    ) -> bool:
        """Push calibrated risk regime advisory to Stratex trading execution engine.

        Target endpoint: POST /v1/signals/risk-regime
        """
        breaker = peer_circuits.for_peer("Stratex")
        if not breaker.allow_request():
            breaker.total_short_circuits += 1
            PEER_CALL_TOTAL.labels(peer="Stratex", outcome="short_circuited").inc()
            logger.warning(
                "stratex_risk_regime_short_circuited",
                circuit_state=breaker.state.value,
            )
            return False

        stratex_url = getattr(settings, "STRATEX_URL", "http://stratex:8000").rstrip("/")
        url = f"{stratex_url}/v1/signals/risk-regime"
        stratex_key = getattr(settings, "STRATEX_API_KEY", "")

        headers = {
            "Authorization": f"Bearer {stratex_key}",
            "X-Caller-Agent": "futuris",
            "Content-Type": "application/json",
        }

        body = {
            "signal_id": str(uuid4()),
            "source_agent": "futuris",
            "target": target,
            "timestamp": datetime.now(UTC).isoformat(),
            "signal_type": "tail_risk_regime",
            "advisory_notice": "prediction_is_not_authorization",
            "risk_metrics": risk_report,
        }

        breaker.total_calls += 1
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                resp = await client.post(url, json=body, headers=headers)
                if resp.status_code in {200, 201, 202}:
                    breaker.record_success()
                    PEER_CALL_TOTAL.labels(peer="Stratex", outcome="ok").inc()
                    logger.info(
                        "stratex_risk_regime_broadcast_success",
                        target=target,
                        status=resp.status_code,
                    )
                    return True
                if resp.status_code >= 500:
                    breaker.record_failure(f"http_{resp.status_code}")
                    PEER_CALL_TOTAL.labels(peer="Stratex", outcome="failed").inc()
                else:
                    breaker.record_success()
                    PEER_CALL_TOTAL.labels(peer="Stratex", outcome="ok").inc()
                logger.warning("stratex_risk_regime_broadcast_non_200", status=resp.status_code)
                return False
        except Exception as exc:
            breaker.record_failure(f"{type(exc).__name__}: {exc}")
            PEER_CALL_TOTAL.labels(peer="Stratex", outcome="failed").inc()
            logger.warning("stratex_risk_regime_broadcast_failed", error=str(exc))
            return False


signal_dispatcher = SignalDispatcher()
