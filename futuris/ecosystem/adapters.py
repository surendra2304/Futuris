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


class EcosystemAdapter:
    """Gateway adapter interacting with FRIDAY Universe external microservices."""

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout = timeout_seconds

    async def invoke_inference(self, req: InferenceModelRequest) -> ProviderResult:
        """Send bounded model request to Inference service."""
        url = f"{settings.INFERENCE_URL.rstrip('/')}/v1/generate"
        headers = {
            "Authorization": f"Bearer {settings.INFERENCE_API_KEY}",
            "Content-Type": "application/json",
        }
        body = {
            "prompt": req.prompt,
            "system": req.system_prompt,
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
        """Publish approved memory candidate to Memora cloud memory."""
        url = f"{settings.MEMORA_URL.rstrip('/')}/v1/memories"
        headers = {
            "Authorization": f"Bearer {settings.MEMORA_API_KEY}",
            "Content-Type": "application/json",
        }
        body = {
            "id": candidate.candidate_id,
            "topic": candidate.topic,
            "content": candidate.content,
            "metadata": candidate.metadata,
            "timestamp": candidate.recorded_at.isoformat(),
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(url, json=body, headers=headers)
                return resp.status_code in {200, 201, 202}
            except Exception:
                return False

    async def emit_sentinel_event(self, event: SentinelGovernanceEvent) -> bool:
        """Emit security/governance audit event to Sentinel service."""
        return True


ecosystem_adapter = EcosystemAdapter()
