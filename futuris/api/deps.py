"""FastAPI dependency injection utilities for storage sessions and domain repositories."""

from collections.abc import AsyncGenerator

from fastapi import Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from futuris.api.errors import FuturisAPIError
from futuris.core.lifecycle import LifecycleManager
from futuris.infra.events import EventEmitter, event_emitter
from futuris.storage import db as storage_db
from futuris.storage.db import safe_rollback
from futuris.storage.repositories import (
    EventRepository,
    ForecastRepository,
    ModelRegistryRepository,
    OutcomeRepository,
    ScenarioRepository,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional async database session.

    The teardown contract matters as much as the transaction itself: it runs
    after the handler has produced a response, so raising there cannot be
    converted into a JSON envelope -- it aborts the connection and the client
    sees ``httpx.ReadError`` instead of the error the API meant to send.  Two
    concrete failures are covered by ``tests/api/test_session_teardown.py``:

    * a failed flush leaves the session in "pending rollback" state; committing
      it raised ``PendingRollbackError`` from this generator;
    * a handler that swallowed a storage error returned a success response for
      work that was never persisted.

    Both are now resolved here: the session is rolled back first (so teardown is
    always able to finish), and a session that cannot commit raises an explicit
    storage error rather than reporting a false success.
    """
    # Resolved at call time: a request session must use the process's current
    # storage factory, not the one that existed when this module was imported (B23).
    async with storage_db.async_session_factory() as session:
        try:
            yield session
        except Exception:
            await safe_rollback(session)
            raise
        try:
            if not session.is_active:
                # ``is_active`` is False exactly when a flush failed and the
                # session is waiting for a rollback.  If we get here the handler
                # returned a response anyway, i.e. it reported success for work
                # that was never committed.  Roll back and report the failure
                # instead of letting ``commit()`` raise ``PendingRollbackError``
                # from this teardown (which aborted the connection and turned a
                # 409 envelope into ``httpx.ReadError``).
                await safe_rollback(session)
                raise FuturisAPIError(
                    code="storage_write_aborted",
                    message=(
                        "The request was not committed: a storage failure occurred while "
                        "writing and the handler did not surface it."
                    ),
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    details={"remediation": "retry the request; check logs for the storage error"},
                )
            await session.commit()
        except Exception:
            await safe_rollback(session)
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
