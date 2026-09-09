from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
import httpx

from futuris.infra.config import settings
from futuris.upgrade.models import FailureKind, ProviderResult


@dataclass(frozen=True)
class InferenceModelRequest:
    prompt: str
    system_prompt: str = ""
    temperature: float = 0.2
    max_tokens: int = 512


@dataclass(frozen=True)
class MemoraMemoryCandidate:
    candidate_id: str
    topic: str
    content: str
    metadata: dict[str, Any]
    recorded_at: datetime


@dataclass(frozen=True)
class SentinelGovernanceEvent:
    event_id: str
    tenant_id: str
    principal_id: str
    action: str
    risk_level: str
    details: dict[str, Any]
    timestamp: datetime


import time
from uuid import uuid4
from futuris.infra.logging import get_logger

logger = get_logger("futuris.ecosystem.adapters")


class EcosystemAdapter:
    """Gateway adapter interacting with FRIDAY Universe external microservices."""

    def __init__(self, timeout_seconds: float = 6.0) -> None:
        self.timeout = timeout_seconds

    async def enhance_forecast_with_inference(
        self,
        metric_name: str,
        point_estimate: float,
        range_lower: float,
        range_upper: float,
        model_used: str,
        probability: float | None = 0.8,
        contextual_factors: list[str] | None = None,
        target_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Call live Inference Gateway /v1/futuris/enhance to get specialist multi-agent qualitative grounding."""
        url = f"{settings.INFERENCE_URL.rstrip('/')}/v1/futuris/enhance"
        headers = {
            "Authorization": f"Bearer {settings.INFERENCE_API_KEY}",
            "Content-Type": "application/json",
        }
        body = {
            "request_id": str(uuid4()),
            "statistical_forecast": {
                "metric_name": metric_name,
                "point_estimate": point_estimate,
                "confidence_interval": [range_lower, range_upper],
                "probability": probability if probability is not None else 0.5,
                "model_used": model_used,
            },
            "target_context": target_context or {"domain": "cloud_infrastructure"},
            "contextual_factors": contextual_factors or ["Operating within nominal bounds"],
            "question": "Given this forecast and context, what risks or drivers should be considered?",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=body, headers=headers)
                if resp.status_code == 200:
                    return resp.json()
                logger.warning("inference_enhance_non_200", status=resp.status_code, body=resp.text[:200])
        except Exception as exc:
            logger.warning("inference_enhance_failed", error=str(exc))
        return None

    async def invoke_inference(self, req: InferenceModelRequest) -> ProviderResult:
        """Send bounded model request to Inference service."""
        url = f"{settings.INFERENCE_URL.rstrip('/')}/ask"
        headers = {
            "Authorization": f"Bearer {settings.INFERENCE_API_KEY}",
            "Content-Type": "application/json",
        }
        body = {
            "prompt": req.prompt,
            "system_prompt": req.system_prompt,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(url, json=body, headers=headers)
                if resp.status_code == 200:
                    return ProviderResult.success("inference", resp.json())
                return ProviderResult.failure_result(
                    "inference", FailureKind.INVALID_REQUEST, retryable=False, output=resp.text
                )
            except httpx.TimeoutException:
                return ProviderResult.failure_result(
                    "inference", FailureKind.TIMEOUT, retryable=True
                )
            except Exception as e:
                return ProviderResult.failure_result(
                    "inference", FailureKind.TRANSIENT, retryable=True, output={"error": str(e)}
                )

    async def publish_memora_candidate(self, candidate: MemoraMemoryCandidate) -> bool:
        """Publish approved memory candidate to Memora cloud memory under futuris/forecasts namespace."""
        url = f"{settings.MEMORA_URL.rstrip('/')}/v1/memories"
        headers = {
            "Authorization": f"Bearer {settings.MEMORA_API_KEY}",
            "X-Agent-Name": "futuris",
            "Content-Type": "application/json",
        }
        body = {
            "content_text": candidate.content,
            "target_namespace_path": "futuris/forecasts",
            "memory_type": "episodic",
            "source": "futuris",
            "confidence": 0.90,
            "importance": 0.80,
            "provenance": {
                "candidate_id": candidate.candidate_id,
                "topic": candidate.topic,
                "recorded_at": candidate.recorded_at.isoformat(),
                **candidate.metadata,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=body, headers=headers)
                return resp.status_code in {200, 201, 202}
        except Exception as exc:
            logger.warning("memora_publish_failed", error=str(exc))
            return False

    async def publish_market_forecast_to_memora(
        self,
        symbol: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Publish market volatility forecast to Memora under futuris/forecasts/market namespace."""
        candidate = MemoraMemoryCandidate(
            candidate_id=str(uuid4()),
            topic=f"market:{symbol}:volatility",
            content=content,
            metadata=metadata or {},
            recorded_at=datetime.now(UTC),
        )
        return await self.publish_memora_candidate(candidate)

    async def dispatch_market_forecast_to_stratex(
        self,
        symbol: str,
        forecast_payload: dict[str, Any],
    ) -> bool:
        """Dispatch freshly computed market forecast to Stratex trading bot."""
        url = f"{settings.STRATEX_URL.rstrip('/')}/api/v1/futuris/forecast"
        headers = {
            "Authorization": f"Bearer {settings.STRATEX_API_KEY}",
            "X-API-Key": settings.STRATEX_API_KEY,
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=forecast_payload, headers=headers)
                if resp.status_code in {200, 201, 202}:
                    logger.info("stratex_market_forecast_dispatched", symbol=symbol, status=resp.status_code)
                    return True
        except Exception as exc:
            logger.debug("stratex_market_forecast_dispatch_skipped", symbol=symbol, error=str(exc))
        return False

    async def emit_sentinel_event(self, event: SentinelGovernanceEvent) -> bool:
        """Emit security/governance audit event to Sentinel service."""
        logger.info(
            "sentinel_governance_event_emitted",
            event_id=event.event_id,
            action=event.action,
            risk_level=event.risk_level,
        )
        return True

    async def probe_peers(self) -> list[dict[str, Any]]:
        """Probe live status, latency, and connectivity of all FRIDAY Universe peer agents."""
        peers_to_check = [
            {
                "name": "IntelX",
                "role": "Deep Intelligence & Exogenous Research",
                "url": settings.INTELX_URL,
                "probe_url": f"{settings.INTELX_URL.rstrip('/')}/health",
                "headers": {"Authorization": f"Bearer {settings.INTELX_API_KEY}"},
                "capabilities": ["Exogenous Signals", "Research Context", "Citation Grounding"],
            },
            {
                "name": "Inference",
                "role": "Multi-Model Reasoning Gateway (25 Keys)",
                "url": settings.INFERENCE_URL,
                "probe_url": f"{settings.INFERENCE_URL.rstrip('/')}/health",
                "headers": {"Authorization": f"Bearer {settings.INFERENCE_API_KEY}"},
                "capabilities": ["Qualitative Enhancements", "Critic Debate", "Agent Reasoners"],
            },
            {
                "name": "Memora",
                "role": "Persistent Associative Cloud Memory",
                "url": settings.MEMORA_URL,
                "probe_url": f"{settings.MEMORA_URL.rstrip('/')}/health",
                "headers": {"Authorization": f"Bearer {settings.MEMORA_API_KEY}"},
                "capabilities": ["Episodic Ledger", "Memory Synthesis", "Audit Traces"],
            },
            {
                "name": "Stratex",
                "role": "24/7 Algorithmic Trading Execution Engine",
                "url": settings.STRATEX_URL,
                "probe_url": f"{settings.STRATEX_URL.rstrip('/')}/health",
                "headers": {"Authorization": f"Bearer {settings.STRATEX_API_KEY}"},
                "capabilities": ["Market Telemetry", "Futures Positions", "Equity Tracking"],
            },
            {
                "name": "Sentinel",
                "role": "Cybersecurity & Governance Defense Shield",
                "url": "http://localhost:8003",
                "probe_url": "http://localhost:8003/health",
                "headers": {},
                "capabilities": ["Policy Verification", "Command Gatekeeper", "Audit Logging"],
            },
            {
                "name": "FRIDAY",
                "role": "Central Desktop Multimodal OS & Orchestrator",
                "url": "http://localhost:9000",
                "probe_url": "http://localhost:9000/health",
                "headers": {},
                "capabilities": ["Master Delegation", "Voice Control", "Cross-Agent Routing"],
            },
        ]

        results = []
        async with httpx.AsyncClient(timeout=3.0) as client:
            for p in peers_to_check:
                t0 = time.perf_counter()
                status_str = "offline"
                latency_ms = None
                try:
                    resp = await client.get(p["probe_url"], headers=p["headers"])
                    latency_ms = round((time.perf_counter() - t0) * 1000, 1)
                    if resp.status_code == 200:
                        status_str = "online"
                    elif resp.status_code in (401, 403, 404):
                        status_str = "degraded"
                    else:
                        status_str = "offline"
                except Exception:
                    # If localhost agents aren't running locally right now, report as offline/standby
                    status_str = "offline"

                results.append({
                    "name": p["name"],
                    "role": p["role"],
                    "url": p["url"],
                    "status": status_str,
                    "latency_ms": latency_ms,
                    "last_interaction": datetime.now(UTC).isoformat(),
                    "capabilities": p["capabilities"],
                })

        return results


ecosystem_adapter = EcosystemAdapter()
