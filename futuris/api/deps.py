"""FastAPI dependency injection utilities for storage sessions and domain repositories."""

from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from futuris.core.lifecycle import LifecycleManager
from futuris.infra.events import EventEmitter, event_emitter
from futuris.storage.db import async_session_factory
from futuris.storage.repositories import (
    EventRepository,
    ForecastRepository,
    ModelRegistryRepository,
    OutcomeRepository,
    ScenarioRepository,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide transactional async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_forecast_repo(session: AsyncSession = Depends(get_db_session)) -> ForecastRepository:
    return ForecastRepository(session)


def get_outcome_repo(session: AsyncSession = Depends(get_db_session)) -> OutcomeRepository:
    return OutcomeRepository(session)


def get_event_repo(session: AsyncSession = Depends(get_db_session)) -> EventRepository:
    return EventRepository(session)


def get_model_repo(session: AsyncSession = Depends(get_db_session)) -> ModelRegistryRepository:
    return ModelRegistryRepository(session)


def get_scenario_repo(session: AsyncSession = Depends(get_db_session)) -> ScenarioRepository:
    return ScenarioRepository(session)


def get_event_emitter() -> EventEmitter:
    return event_emitter


def get_lifecycle_manager(
    f_repo: ForecastRepository = Depends(get_forecast_repo),
    o_repo: OutcomeRepository = Depends(get_outcome_repo),
    e_repo: EventRepository = Depends(get_event_repo),
    emitter: EventEmitter = Depends(get_event_emitter),
) -> LifecycleManager:
    return LifecycleManager(
        forecast_repo=f_repo,
        outcome_repo=o_repo,
        event_repo=e_repo,
        emitter=emitter,
    )
