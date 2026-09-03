from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .forecast_guard import ProductionDataSource, SourcePolicy, normalize_as_of
from .models import ForecastEnvelope, Principal
from .policy import PolicyEngine, PolicyViolation
from .quality import ForecastQualityGate


class ExistingForecastEngine(Protocol):
    async def orchestrate(
        self, target: str, as_of: datetime, horizon: timedelta, **kwargs: Any
    ) -> list[Any]: ...


@dataclass(frozen=True)
class FuturisRuntimeContext:
    principal: Principal
    policy: PolicyEngine
    source_policy: SourcePolicy


class ForecastCompatibilityAdapter:
    """Wrapper contract for integrating hardened gates around existing ForecastEngine."""

    def __init__(self, engine: ExistingForecastEngine, context: FuturisRuntimeContext) -> None:
        self.engine = engine
        self.context = context
        self.quality = ForecastQualityGate()

    async def forecast(
        self,
        *,
        target: str,
        as_of: datetime,
        horizon: timedelta,
        source: ProductionDataSource,
    ) -> list[ForecastEnvelope]:
        horizon_hours = horizon.total_seconds() / 3600
        policy = self.context.policy.evaluate_forecast(horizon_hours, max(1, int(horizon.days) + 1))
        if not policy.allowed:
            raise PolicyViolation(policy.reason)
        self.context.source_policy.validate(source)
        as_of = normalize_as_of(as_of)

        raw_results = await self.engine.orchestrate(
            target=target,
            as_of=as_of,
            horizon=horizon,
            evidence_scope=source.name,
        )
        envelopes: list[ForecastEnvelope] = []
        for raw in raw_results:
            env = ForecastEnvelope(
                forecast_id=raw.forecast_id,
                target=raw.target,
                as_of=normalize_as_of(raw.as_of),
                prediction=float(raw.prediction),
                lower=float(raw.range_lower),
                upper=float(raw.range_upper),
                probability=float(raw.probability) if raw.probability is not None else None,
                confidence=_confidence_to_float(raw.confidence),
                model_version=str(raw.model_version),
                evidence_ids=[str(e.evidence_id) for e in raw.evidence],
                assumptions=list(raw.assumptions),
                source=source.name,
            )
            self.quality.require(env)
            envelopes.append(env)
        return envelopes


def _confidence_to_float(value: Any) -> float:
    raw = getattr(value, "value", value)
    mapping = {"low": 0.35, "medium": 0.65, "high": 0.9}
    if isinstance(raw, str):
        return mapping.get(raw.lower(), 0.0)
    return float(raw)
