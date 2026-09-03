from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


class JobState(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FailureKind(str, Enum):
    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    AUTHENTICATION = "authentication"
    POLICY = "policy"
    INVALID_REQUEST = "invalid_request"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "timeout"
    DATA_QUALITY = "data_quality"
    INTERNAL = "internal"


class ActionRisk(str, Enum):
    OBSERVE = "observe"
    ADVISORY = "advisory"
    GOVERNED = "governed"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True)
class Principal:
    principal_id: str
    tenant_id: str
    roles: frozenset[str] = frozenset()
    scopes: frozenset[str] = frozenset()
    credential_id: str | None = None


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    principal: Principal
    idempotency_key: str | None = None


@dataclass(frozen=True)
class Cost:
    currency: str
    amount: Decimal
    provider: str
    operation: str


@dataclass
class ProviderResult:
    provider: str
    ok: bool
    output: Any = None
    failure: FailureKind | None = None
    retryable: bool = False
    latency_ms: float = 0.0
    request_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, provider: str, output: Any, **kwargs: Any) -> "ProviderResult":
        return cls(provider=provider, ok=True, output=output, **kwargs)

    @classmethod
    def failure_result(
        cls, provider: str, failure: FailureKind, *, retryable: bool, output: Any = None, **kwargs: Any
    ) -> "ProviderResult":
        return cls(
            provider=provider,
            ok=False,
            output=output,
            failure=failure,
            retryable=retryable,
            **kwargs,
        )


@dataclass
class ForecastEnvelope:
    forecast_id: UUID = field(default_factory=uuid4)
    target: str = ""
    as_of: datetime = field(default_factory=utc_now)
    prediction: float = 0.0
    lower: float = 0.0
    upper: float = 0.0
    probability: float | None = None
    confidence: float = 0.0
    model_version: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    source: str = ""
    quality: dict[str, Any] = field(default_factory=dict)


@dataclass
class DecisionRecord:
    decision_id: UUID = field(default_factory=uuid4)
    forecast_id: UUID | None = None
    action: str = ""
    risk: ActionRisk = ActionRisk.ADVISORY
    rationale: str = ""
    confidence: float = 0.0
    evidence_ids: list[str] = field(default_factory=list)
    requires_authorization: bool = True
    approved_by: str | None = None
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class ResearchOrForecastJob:
    job_id: UUID = field(default_factory=uuid4)
    tenant_id: str = ""
    principal_id: str = ""
    target: str = ""
    state: JobState = JobState.CREATED
    version: int = 0
    attempts: int = 0
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditEvent:
    event_id: UUID = field(default_factory=uuid4)
    tenant_id: str = ""
    principal_id: str = ""
    action: str = ""
    resource: str = ""
    outcome: str = ""
    reason: str = ""
    request_id: str | None = None
    at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)
