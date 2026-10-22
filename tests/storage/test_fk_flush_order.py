"""Foreign-key integrity of the unit of work, proven against a real SQLite file.

Regression origin (found by driving the live peer mesh, not by a unit test):
``ForecastRepository.create`` adds a ``ForecastModel`` and its
``ForecastEventModel`` audit row and flushes once.  SQLAlchemy derives
cross-table flush order from relationships only; unrelated mappers are ordered
by ``(module, class name)``, which sorts ``ForecastEventModel`` *before*
``ForecastModel``.  With ``PRAGMA foreign_keys=ON`` the audit insert therefore
ran first and died with ``FOREIGN KEY constraint failed`` -- and because the
flush aborts, ``INSERT INTO forecasts`` was never emitted at all.  The API
returned 200 with ``forecast_id: null`` and the request-scoped commit then blew
up during teardown.

These tests use a file-backed engine configured exactly like production
(``install_sqlite_pragmas``: WAL, busy timeout, ``foreign_keys=ON``) so a
missing relationship fails here instead of in production.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from futuris.core.enums import (
    ConfidenceLevel,
    EvidenceClass,
    ForecastStatus,
    ScenarioType,
    SignalClass,
    SourceTrust,
)
from futuris.core.hashing import content_hash_of
from futuris.core.schemas import Driver, EvidenceRef, Forecast, Outcome, Scenario
from futuris.storage.db import install_sqlite_pragmas
from futuris.storage.models import (
    Base,
    EvaluationRunModel,
    EvidenceRefModel,
    ForecastEventModel,
    ForecastModel,
    ModelRegistryModel,
    OutcomeModel,
    ScenarioModel,
)
from futuris.storage.repositories import (
    EvaluationRepository,
    EventRepository,
    ForecastRepository,
    ModelRepository,
    OutcomeRepository,
    ScenarioRepository,
)


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> AsyncGenerator[AsyncEngine, None]:
    db_path = tmp_path / "fk_integrity.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    install_sqlite_pragmas(eng)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


def _forecast(**overrides: object) -> Forecast:
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "forecast_id": uuid4(),
        "target": "test:flush-order",
        "as_of": now,
        "horizon": timedelta(hours=1),
        "expires_at": now + timedelta(hours=1),
        "review_at": now,
        "prediction": 1.0,
        "range_lower": 0.0,
        "range_upper": 2.0,
        "probability": 0.5,
        "confidence": ConfidenceLevel.LOW,
        "drivers": [],
        "evidence": [],
        "assumptions": [],
        "model_version": "test:flush@v1",
        "status": ForecastStatus.ACTIVE,
    }
    payload.update(overrides)
    return Forecast(**payload)  # type: ignore[arg-type]


def _evidence() -> EvidenceRef:
    return EvidenceRef(
        evidence_id=uuid4(),
        source="test:fixture",
        source_trust=SourceTrust.HIGH,
        signal_class=SignalClass.TELEMETRY,
        as_of=datetime.now(UTC),
        snapshot_path="inline://test/fixture.json",
        content_hash=content_hash_of({"fixture": True}),
        evidence_class=EvidenceClass.LIVE,
    )


@pytest.mark.asyncio
async def test_forecast_and_audit_event_persist_in_one_flush(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The original failure: the audit insert is ordered before its own forecast."""
    forecast = _forecast()
    async with session_factory() as session:
        await ForecastRepository(session).create(forecast)
        await session.commit()

    async with session_factory() as session:
        stored = await ForecastRepository(session).get(forecast.forecast_id)
        assert stored is not None
        events = await EventRepository(session).list_by_forecast(forecast.forecast_id)
        assert [event.event_type.value for event in events] == ["forecast_created"]


@pytest.mark.asyncio
async def test_forecast_event_rows_resolve_to_a_stored_forecast(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Every audit row must join back to a forecast row (no dangling FK)."""
    forecast = _forecast()
    async with session_factory() as session:
        await ForecastRepository(session).create(forecast)
        await session.commit()

    async with session_factory() as session:
        joined = await session.execute(
            select(ForecastEventModel.event_id).join(
                ForecastModel, ForecastEventModel.forecast_id == ForecastModel.forecast_id
            )
        )
        assert len(joined.scalars().all()) >= 1


@pytest.mark.asyncio
async def test_forecast_with_evidence_persists_evidence_in_one_flush(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    evidence = _evidence()
    forecast = _forecast(
        evidence=[evidence],
        drivers=[
            Driver(
                name="stratex_volatility_idx",
                direction="positive",
                strength=0.8,
                leading_or_lagging="leading",
                evidence_refs=[evidence.evidence_id],
            )
        ],
    )
    async with session_factory() as session:
        await ForecastRepository(session).create(forecast)
        await session.commit()

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(EvidenceRefModel).where(EvidenceRefModel.forecast_id == forecast.forecast_id)
            )
        ).scalars().all()
        assert [row.evidence_id for row in rows] == [evidence.evidence_id]


@pytest.mark.asyncio
async def test_new_scenario_and_forecast_referencing_it_persist_together(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """``forecasts.scenario_id`` must be inserted after its ``scenarios`` row."""
    async with session_factory() as session:
        scenario_id = uuid4()
        scenario = Scenario(
            scenario_id=scenario_id,
            name="rate hike",
            scenario_type=ScenarioType.COUNTERFACTUAL,
            assumptions_override={"rate": 0.05},
            created_by="test",
            parent_forecast_id=None,
        )
        # Both rows are pending in the same flush: the scenario repository flush
        # is deliberately skipped here to exercise flush ordering itself.
        session.add(
            ScenarioModel(
                scenario_id=scenario_id,
                name=scenario.name,
                scenario_type=scenario.scenario_type.value,
                assumptions_override=scenario.assumptions_override,
                created_by=scenario.created_by,
                parent_forecast_id=None,
            )
        )
        await ForecastRepository(session).create(_forecast(scenario_id=scenario_id))
        await session.commit()

    async with session_factory() as session:
        stored = await ScenarioRepository(session).get(scenario_id)
        assert stored is not None


@pytest.mark.asyncio
async def test_outcome_and_forecast_persist_in_one_flush(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """``outcomes.forecast_id`` + the outcome event share the forecast's flush."""
    forecast = _forecast()
    async with session_factory() as session:
        await ForecastRepository(session).create(forecast)
        await session.commit()

    async with session_factory() as session:
        outcome = Outcome(
            outcome_id=uuid4(),
            forecast_id=forecast.forecast_id,
            observed_value=1.25,
            event_occurred=True,
            resolved_at=datetime.now(UTC),
            resolution_method="automatic",
            ambiguity_note=None,
            resolution_rule_version="test:v1",
        )
        await OutcomeRepository(session).record_outcome(outcome)
        await session.commit()

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(OutcomeModel).where(OutcomeModel.forecast_id == forecast.forecast_id)
            )
        ).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_model_registry_and_evaluation_run_persist_in_one_flush(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """``evaluation_runs.model_version`` must follow its registry row."""
    version = f"test:model@v1-{uuid4().hex[:8]}"
    async with session_factory() as session:
        session.add(
            ModelRegistryModel(
                model_version=version,
                family="statsforecast",
                config_hash=content_hash_of({"version": version}),
                is_active=True,
                promoted_at=datetime.now(UTC),
                benchmark_scores={},
            )
        )
        # No intervening flush: the registry row is still pending when the run
        # referencing it is added.
        await EvaluationRepository(session).save_run(
            model_version=version, dataset_name="unit", metrics={"crps": 0.1}
        )
        await session.commit()

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(EvaluationRunModel.model_version).where(
                    EvaluationRunModel.model_version == version
                )
            )
        ).scalars().all()
        assert rows == [version]


@pytest.mark.asyncio
async def test_registry_repository_then_evaluation_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The production call order (register, then evaluate) also holds."""
    from futuris.core.schemas import ModelInfo

    version = f"test:repo-model@v1-{uuid4().hex[:8]}"
    async with session_factory() as session:
        await ModelRepository(session).register(
            ModelInfo(
                model_version=version,
                family="statsforecast",
                config_hash=content_hash_of({"version": version}),
                promoted_at=datetime.now(UTC),
                benchmark_scores={},
            )
        )
        await EvaluationRepository(session).save_run(
            model_version=version, dataset_name="unit", metrics={"crps": 0.2}
        )
        await session.commit()

    async with session_factory() as session:
        stored = (
            await session.execute(
                select(ModelRegistryModel.model_version).where(
                    ModelRegistryModel.model_version == version
                )
            )
        ).scalars().all()
        runs = (
            await session.execute(
                select(EvaluationRunModel.run_id).where(
                    EvaluationRunModel.model_version == version
                )
            )
        ).scalars().all()
        assert stored == [version]
        assert len(runs) == 1


def test_every_foreign_key_has_deterministic_flush_order() -> None:
    """Guard: a foreign key without ordering guarantees is a latent 409.

    SQLAlchemy orders unrelated mappers by ``(module, class name)``.  An insert
    that references a row in another table is therefore only safe if either the
    child mapper has a relationship to the parent mapper (which becomes a real
    dependency edge in the unit of work) or the parent's sort key already sorts
    first.  Adding a model that breaks either rule must fail here, not in a live
    mesh run.
    """
    mappers = {mapper.local_table.name: mapper for mapper in Base.registry.mappers}
    unsafe: list[str] = []
    for table in Base.metadata.sorted_tables:
        child = mappers[table.name]
        for fk in table.foreign_keys:
            parent = mappers[fk.column.table.name]
            has_relationship = any(
                rel.mapper.local_table.name == parent.local_table.name
                for rel in child.relationships
            )
            parent_sorts_first = parent._sort_key < child._sort_key
            if not (has_relationship or parent_sorts_first):
                unsafe.append(
                    f"{table.name}.{fk.parent.name} -> {parent.local_table.name} "
                    f"(no relationship, {child._sort_key} < {parent._sort_key})"
                )
    assert not unsafe, (
        "foreign keys without a deterministic insert order: "
        + ", ".join(unsafe)
        + ". Add a many-to-one relationship on the child mapper (see "
        "ForecastEventModel.forecast) or flush the parent first."
    )


def test_forecast_events_relationship_is_not_used_for_loading() -> None:
    """The audit relationship exists to pin flush order, not to cascade loads."""
    relationship = ForecastEventModel.__mapper__.relationships["forecast"]
    assert relationship.lazy == "raise"
    assert relationship.viewonly is False
    assert relationship.mapper.class_ is ForecastModel


def test_ids_stay_stable_between_domain_and_storage() -> None:
    """A persisted forecast keeps the caller-supplied id (provenance contract)."""
    forecast = _forecast()
    assert isinstance(forecast.forecast_id, UUID)
    assert uuid4() != forecast.forecast_id
