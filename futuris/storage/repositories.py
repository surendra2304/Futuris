"""Asynchronous repository pattern implementations for FUTURIS persistence."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from futuris.core.enums import (
    ConfidenceLevel,
    ForecastEventType,
    ForecastStatus,
    ResolutionMethod,
    SignalClass,
    SourceTrust,
)
from futuris.core.schemas import (
    Driver,
    EvidenceRef,
    Forecast,
    ForecastEvent,
    ModelInfo,
    Outcome,
    Scenario,
)
from futuris.storage.models import (
    EvaluationRunModel,
    EvidenceRefModel,
    ForecastEventModel,
    ForecastModel,
    ModelRegistryModel,
    OutcomeModel,
    ScenarioModel,
)


class ReadOnlyAuditViolationError(Exception):
    """Raised when an illegal mutation or deletion is attempted on an append-only audit log."""


class ModelPromotionError(Exception):
    """Raised when a model is promoted without required benchmark scores."""


class ForecastRepository:
    """Repository managing Forecast aggregates and point-in-time state queries."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_domain(self, model: ForecastModel) -> Forecast:
        evidence_list = [
            EvidenceRef(
                evidence_id=e.evidence_id,
                source=e.source,
                source_trust=SourceTrust(e.source_trust),
                signal_class=SignalClass(e.signal_class),
                as_of=e.as_of,
                snapshot_path=e.snapshot_path,
                content_hash=e.content_hash,
            )
            for e in model.evidence
        ]
        drivers_list = [
            Driver(
                name=d["name"],
                direction=d["direction"],
                strength=d["strength"],
                leading_or_lagging=d["leading_or_lagging"],
                evidence_refs=[UUID(ref) for ref in d.get("evidence_refs", [])],
            )
            for d in model.drivers
        ]
        return Forecast(
            forecast_id=model.forecast_id,
            target=model.target,
            as_of=model.as_of,
            horizon=model.horizon,
            expires_at=model.expires_at,
            prediction=model.prediction,
            range_lower=model.range_lower,
            range_upper=model.range_upper,
            probability=model.probability,
            confidence=ConfidenceLevel(model.confidence),
            drivers=drivers_list,
            evidence=evidence_list,
            model_version=model.model_version,
            assumptions=model.assumptions,
            review_at=model.review_at,
            status=ForecastStatus(model.status),
            scenario_id=model.scenario_id,
        )

    async def create(self, forecast: Forecast) -> Forecast:
        now = datetime.now(UTC)
        evidence_models = [
            EvidenceRefModel(
                evidence_id=e.evidence_id,
                source=e.source,
                source_trust=e.source_trust.value,
                signal_class=e.signal_class.value,
                as_of=e.as_of,
                snapshot_path=e.snapshot_path,
                content_hash=e.content_hash,
            )
            for e in forecast.evidence
        ]
        drivers_payload = [
            {
                "name": d.name,
                "direction": d.direction,
                "strength": d.strength,
                "leading_or_lagging": d.leading_or_lagging,
                "evidence_refs": [str(ref) for ref in d.evidence_refs],
            }
            for d in forecast.drivers
        ]

        model = ForecastModel(
            forecast_id=forecast.forecast_id,
            target=forecast.target,
            as_of=forecast.as_of,
            horizon=forecast.horizon,
            expires_at=forecast.expires_at,
            prediction=forecast.prediction,
            range_lower=forecast.range_lower,
            range_upper=forecast.range_upper,
            probability=forecast.probability,
            confidence=forecast.confidence.value,
            drivers=drivers_payload,
            model_version=forecast.model_version,
            assumptions=forecast.assumptions,
            review_at=forecast.review_at,
            status=forecast.status.value,
            scenario_id=forecast.scenario_id,
            created_at=now,
            updated_at=now,
            evidence=evidence_models,
        )
        self.session.add(model)

        # Record forecast_created event in audit trail
        event = ForecastEventModel(
            forecast_id=forecast.forecast_id,
            event_type=ForecastEventType.FORECAST_CREATED.value,
            payload=forecast.model_dump(mode="json"),
            emitted_at=now,
        )
        self.session.add(event)
        await self.session.flush()
        return self._to_domain(model)

    async def get(self, forecast_id: UUID) -> Forecast | None:
        stmt = select(ForecastModel).where(ForecastModel.forecast_id == forecast_id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def list_by_target(
        self, target: str, as_of_range: tuple[datetime, datetime] | None = None
    ) -> list[Forecast]:
        stmt = select(ForecastModel).where(ForecastModel.target == target)
        if as_of_range:
            start, end = as_of_range
            stmt = stmt.where(ForecastModel.as_of >= start, ForecastModel.as_of <= end)
        stmt = stmt.order_by(ForecastModel.as_of.desc())
        result = await self.session.execute(stmt)
        return [self._to_domain(m) for m in result.scalars().all()]

    async def list_by_status(self, status: ForecastStatus) -> list[Forecast]:
        stmt = (
            select(ForecastModel)
            .where(ForecastModel.status == status.value)
            .order_by(ForecastModel.as_of.desc())
        )
        result = await self.session.execute(stmt)
        return [self._to_domain(m) for m in result.scalars().all()]

    async def update_status(self, forecast_id: UUID, new_status: ForecastStatus) -> Forecast | None:
        now = datetime.now(UTC)
        stmt = (
            update(ForecastModel)
            .where(ForecastModel.forecast_id == forecast_id)
            .values(status=new_status.value, updated_at=now)
        )
        await self.session.execute(stmt)

        # Record forecast_updated event in audit trail
        event = ForecastEventModel(
            forecast_id=forecast_id,
            event_type=ForecastEventType.FORECAST_UPDATED.value,
            payload={"new_status": new_status.value},
            emitted_at=now,
        )
        self.session.add(event)
        await self.session.flush()
        return await self.get(forecast_id)

    async def point_in_time_query(self, target: str, query_time: datetime) -> Forecast | None:
        """Query the state of the latest forecast for a target as it existed at query_time.

        Uses the append-only events log to reconstruct true historical state without
        leaking any subsequent mutations or status updates.
        """
        # Find events targeting this target emitted on or before query_time
        stmt = (
            select(ForecastEventModel)
            .where(ForecastEventModel.emitted_at <= query_time)
            .order_by(ForecastEventModel.emitted_at.desc())
        )
        result = await self.session.execute(stmt)
        events = result.scalars().all()

        matching_event: ForecastEventModel | None = None
        for ev in events:
            if ev.payload.get("target") == target:
                matching_event = ev
                break

        if not matching_event:
            return None

        # Reconstruct forecast from historical snapshot payload
        data = dict(matching_event.payload)
        return Forecast.model_validate(data)


class OutcomeRepository:
    """Repository managing ground truth resolution outcomes."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_domain(self, model: OutcomeModel) -> Outcome:
        return Outcome(
            outcome_id=model.outcome_id,
            forecast_id=model.forecast_id,
            observed_value=model.observed_value,
            event_occurred=model.event_occurred,
            resolved_at=model.resolved_at,
            resolution_method=ResolutionMethod(model.resolution_method),
            ambiguity_note=model.ambiguity_note,
            resolution_rule_version=model.resolution_rule_version,
        )

    async def record_outcome(self, outcome: Outcome) -> Outcome:
        model = OutcomeModel(
            outcome_id=outcome.outcome_id,
            forecast_id=outcome.forecast_id,
            observed_value=outcome.observed_value,
            event_occurred=outcome.event_occurred,
            resolved_at=outcome.resolved_at,
            resolution_method=outcome.resolution_method.value,
            ambiguity_note=outcome.ambiguity_note,
            resolution_rule_version=outcome.resolution_rule_version,
        )
        self.session.add(model)

        # Mark forecast as resolved and emit event
        await self.session.execute(
            update(ForecastModel)
            .where(ForecastModel.forecast_id == outcome.forecast_id)
            .values(status=ForecastStatus.RESOLVED.value, updated_at=datetime.now(UTC))
        )
        event = ForecastEventModel(
            forecast_id=outcome.forecast_id,
            event_type=ForecastEventType.FORECAST_OUTCOME_RECORDED.value,
            payload=outcome.model_dump(mode="json"),
            emitted_at=datetime.now(UTC),
        )
        self.session.add(event)
        await self.session.flush()
        return self._to_domain(model)

    async def get_for_forecast(self, forecast_id: UUID) -> Outcome | None:
        stmt = select(OutcomeModel).where(OutcomeModel.forecast_id == forecast_id)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def get_by_forecast(self, forecast_id: UUID) -> Outcome | None:
        return await self.get_for_forecast(forecast_id)

    async def list_unresolved(self, past_horizon: bool = True) -> list[Forecast]:
        now = datetime.now(UTC)
        stmt = (
            select(ForecastModel)
            .where(ForecastModel.status == ForecastStatus.ACTIVE.value)
        )
        if past_horizon:
            stmt = stmt.where(ForecastModel.expires_at <= now)

        result = await self.session.execute(stmt)
        repo = ForecastRepository(self.session)
        return [repo._to_domain(m) for m in result.scalars().all()]


class EvidenceRepository:
    """Repository storing frozen point-in-time evidence and snapshot metadata."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def store_evidence_ref(
        self, evidence: EvidenceRef, forecast_id: UUID | None = None
    ) -> EvidenceRef:
        model = EvidenceRefModel(
            evidence_id=evidence.evidence_id,
            forecast_id=forecast_id,
            source=evidence.source,
            source_trust=evidence.source_trust.value,
            signal_class=evidence.signal_class.value,
            as_of=evidence.as_of,
            snapshot_path=evidence.snapshot_path,
            content_hash=evidence.content_hash,
        )
        self.session.add(model)
        await self.session.flush()
        return evidence

    async def get(self, evidence_id: UUID) -> EvidenceRef | None:
        stmt = select(EvidenceRefModel).where(EvidenceRefModel.evidence_id == evidence_id)
        result = await self.session.execute(stmt)
        m = result.scalar_one_or_none()
        if not m:
            return None
        return EvidenceRef(
            evidence_id=m.evidence_id,
            source=m.source,
            source_trust=SourceTrust(m.source_trust),
            signal_class=SignalClass(m.signal_class),
            as_of=m.as_of,
            snapshot_path=m.snapshot_path,
            content_hash=m.content_hash,
        )


class EventRepository:
    """Append-only audit trail repository for lifecycle events. Mutation strictly disallowed."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(self, event: ForecastEvent) -> ForecastEvent:
        model = ForecastEventModel(
            event_id=event.event_id,
            forecast_id=event.forecast_id,
            event_type=event.event_type.value,
            payload=event.payload,
            emitted_at=event.emitted_at,
        )
        self.session.add(model)
        await self.session.flush()
        return event

    async def update(self, *args: Any, **kwargs: Any) -> None:
        """Enforce append-only invariant."""
        _ = args, kwargs
        msg = "ForecastEvent audit trail is strictly append-only and cannot be updated."
        raise ReadOnlyAuditViolationError(msg)

    async def delete(self, *args: Any, **kwargs: Any) -> None:
        """Enforce append-only invariant."""
        _ = args, kwargs
        msg = "ForecastEvent audit trail is strictly append-only and cannot be deleted."
        raise ReadOnlyAuditViolationError(msg)

    async def list_by_forecast(self, forecast_id: UUID) -> list[ForecastEvent]:
        stmt = (
            select(ForecastEventModel)
            .where(ForecastEventModel.forecast_id == forecast_id)
            .order_by(ForecastEventModel.emitted_at.asc())
        )
        result = await self.session.execute(stmt)
        return [
            ForecastEvent(
                event_id=m.event_id,
                forecast_id=m.forecast_id,
                event_type=ForecastEventType(m.event_type),
                payload=m.payload,
                emitted_at=m.emitted_at,
            )
            for m in result.scalars().all()
        ]

    async def list_since(self, since: datetime) -> list[ForecastEvent]:
        stmt = (
            select(ForecastEventModel)
            .where(ForecastEventModel.emitted_at >= since)
            .order_by(ForecastEventModel.emitted_at.asc())
        )
        result = await self.session.execute(stmt)
        return [
            ForecastEvent(
                event_id=m.event_id,
                forecast_id=m.forecast_id,
                event_type=ForecastEventType(m.event_type),
                payload=m.payload,
                emitted_at=m.emitted_at,
            )
            for m in result.scalars().all()
        ]


class ModelRepository:
    """Model registry repository with promotion gate enforcement."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def register(self, model_info: ModelInfo) -> ModelInfo:
        model = ModelRegistryModel(
            model_version=model_info.model_version,
            family=model_info.family,
            config_hash=model_info.config_hash,
            is_active=False,
            promoted_at=None,
            benchmark_scores=model_info.benchmark_scores,
        )
        self.session.add(model)
        await self.session.flush()
        return model_info

    async def promote(
        self,
        model_version: str,
        max_benchmark_age_days: int = 30,
    ) -> ModelInfo:
        stmt = select(ModelRegistryModel).where(ModelRegistryModel.model_version == model_version)
        result = await self.session.execute(stmt)
        model = result.scalar_one_or_none()
        if not model:
            msg = f"Model version '{model_version}' not found in registry."
            raise ValueError(msg)

        if not model.benchmark_scores:
            msg = f"Promotion rejected: Model '{model_version}' has no benchmark scores attached."
            raise ModelPromotionError(msg)

        # Check benchmark run freshness
        eval_stmt = (
            select(EvaluationRunModel)
            .where(EvaluationRunModel.model_version == model_version)
            .order_by(EvaluationRunModel.created_at.desc())
        )
        eval_res = await self.session.execute(eval_stmt)
        recent_eval = eval_res.scalars().first()

        now = datetime.now(UTC)
        if recent_eval:
            age_days = (now - recent_eval.created_at).total_seconds() / 86400.0
            if age_days > max_benchmark_age_days:
                msg = (
                    f"Promotion rejected: Benchmark for '{model_version}' is {age_days:.1f} "
                    f"days old (max permitted: {max_benchmark_age_days} days)."
                )
                raise ModelPromotionError(msg)

        # Check performance against current active model in the same family
        active_models = await self.current_active(family=model.family)
        candidate_mae = model.benchmark_scores.get("mae", float("inf"))
        candidate_ece = model.benchmark_scores.get("ece", float("inf"))

        for active_m in active_models:
            if active_m.model_version == model_version:
                continue
            active_mae = active_m.benchmark_scores.get("mae", 0.0)
            active_ece = active_m.benchmark_scores.get("ece", 0.0)

            # Gate: candidate cannot be worse on MAE or calibration error
            if active_mae > 0 and candidate_mae > active_mae:
                msg = (
                    f"Promotion rejected: Candidate MAE ({candidate_mae:.2f}) is worse than "
                    f"current active model '{active_m.model_version}' MAE ({active_mae:.2f})."
                )
                raise ModelPromotionError(msg)
            if active_ece > 0 and candidate_ece > active_ece:
                msg = (
                    f"Promotion rejected: Candidate ECE ({candidate_ece:.4f}) is worse than "
                    f"current active model '{active_m.model_version}' ECE ({active_ece:.4f})."
                )
                raise ModelPromotionError(msg)

        # Deactivate existing active models in the family
        await self.session.execute(
            update(ModelRegistryModel)
            .where(ModelRegistryModel.family == model.family)
            .values(is_active=False)
        )

        model.is_active = True
        model.promoted_at = now
        await self.session.flush()

        return ModelInfo(
            model_version=model.model_version,
            family=model.family,
            config_hash=model.config_hash,
            promoted_at=now,
            benchmark_scores=model.benchmark_scores,
        )

    async def current_active(self, family: str | None = None) -> list[ModelInfo]:
        stmt = select(ModelRegistryModel).where(ModelRegistryModel.is_active.is_(True))
        if family:
            stmt = stmt.where(ModelRegistryModel.family == family)
        result = await self.session.execute(stmt)
        return [
            ModelInfo(
                model_version=m.model_version,
                family=m.family,
                config_hash=m.config_hash,
                promoted_at=m.promoted_at or datetime.now(UTC),
                benchmark_scores=m.benchmark_scores,
            )
            for m in result.scalars().all()
        ]


ModelRegistryRepository = ModelRepository


class EvaluationRepository:
    """Repository storing evaluation benchmarks and backtesting runs."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_run(
        self,
        model_version: str,
        dataset_name: str,
        metrics: dict[str, float],
    ) -> UUID:
        now = datetime.now(UTC)
        run_id = UUID(int=int(now.timestamp() * 1000000))
        model = EvaluationRunModel(
            run_id=run_id,
            model_version=model_version,
            dataset_name=dataset_name,
            metrics=metrics,
            created_at=now,
        )
        self.session.add(model)
        await self.session.flush()
        return run_id

    async def latest_for_model(self, model_version: str) -> dict[str, Any] | None:
        stmt = (
            select(EvaluationRunModel)
            .where(EvaluationRunModel.model_version == model_version)
            .order_by(EvaluationRunModel.created_at.desc())
        )
        result = await self.session.execute(stmt)
        run = result.scalars().first()
        if not run:
            return None
        return {
            "run_id": run.run_id,
            "model_version": run.model_version,
            "dataset_name": run.dataset_name,
            "metrics": run.metrics,
            "created_at": run.created_at,
        }


class ScenarioRepository:
    """Repository storing scenario definitions and counterfactual override graphs."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, scenario: Scenario, parent_forecast_id: UUID | None = None) -> Scenario:
        model = ScenarioModel(
            scenario_id=scenario.scenario_id,
            name=scenario.name,
            scenario_type=scenario.scenario_type.value,
            assumptions_override=scenario.assumptions_override,
            created_by=scenario.created_by,
            parent_forecast_id=parent_forecast_id or scenario.parent_forecast_id,
        )
        self.session.add(model)
        await self.session.flush()
        return scenario

    async def get(self, scenario_id: UUID) -> Scenario | None:
        stmt = select(ScenarioModel).where(ScenarioModel.scenario_id == scenario_id)
        result = await self.session.execute(stmt)
        m = result.scalars().first()
        if not m:
            return None
        from futuris.core.enums import ScenarioType

        return Scenario(
            scenario_id=m.scenario_id,
            name=m.name,
            scenario_type=ScenarioType(m.scenario_type),
            assumptions_override=m.assumptions_override,
            created_by=m.created_by,
            parent_forecast_id=m.parent_forecast_id,
        )
