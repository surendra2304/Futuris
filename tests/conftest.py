"""Shared test fixtures.

Fixtures here exist because a test must control the state it asserts on. The
first version of the health tests did not, and passed on a developer machine
(where ``./data/futuris.db`` already existed from earlier runs) while failing on
a clean CI checkout -- ``/health`` reported ``degraded`` because the process-wide
storage engine had no schema. That is a test bug, not an app bug.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from futuris.storage import db as storage_db
from futuris.storage.db import install_sqlite_pragmas
from futuris.storage.models import Base


@pytest.fixture(scope="session")
def _schema_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One schema-initialised SQLite file per session, copied into every test (D13)."""
    path = tmp_path_factory.mktemp("schema") / "template.db"
    sync_engine = create_sync_engine(f"sqlite:///{path}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()
    return path


@pytest_asyncio.fixture
async def ready_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _schema_template: Path
) -> AsyncGenerator[AsyncEngine, None]:
    """Point the process-wide storage at a temporary, schema-initialised database.

    ``/health`` measures the *real* engine (``futuris.storage.db.engine``), not
    the request-scoped session a test may have overridden, so a test asserting a
    healthy storage verdict has to control that engine explicitly. The database
    is a throwaway file with the full schema, installed exactly like production
    (``install_sqlite_pragmas``), and the original engine is restored afterwards.
    """
    database = tmp_path / "storage_probe.db"
    shutil.copyfile(_schema_template, database)
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    install_sqlite_pragmas(engine)

    monkeypatch.setattr(storage_db, "engine", engine)
    monkeypatch.setattr(
        storage_db,
        "async_session_factory",
        async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False),
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def health_storage(ready_storage: AsyncEngine) -> AsyncEngine:
    """Alias used by the health tests: a booted, schema-ready storage engine."""
    return ready_storage


@pytest_asyncio.fixture(autouse=True)
async def _isolated_storage_for_every_test(
    ready_storage: AsyncEngine,
) -> AsyncGenerator[None, None]:
    """Every test runs against its own throwaway database (D13).

    Before this, a test that did not opt in used ``./data/futuris.db``: its result
    depended on whatever the developer's workspace held, and a server running on the
    same file produced intermittent ``storage_busy`` failures (R16). The default is
    now isolation; a test that needs a different engine still monkeypatches it.
    """
    yield


@pytest_asyncio.fixture(autouse=True)
async def _fresh_request_budgets() -> AsyncGenerator[None, None]:
    """Reset the in-process request budgets so one test's traffic cannot throttle another."""
    from futuris.api.routers.friday import friday_limiter
    from futuris.infra.auth import anonymous_read_limiter

    await anonymous_read_limiter.clear()
    await friday_limiter.clear()
    yield
    await anonymous_read_limiter.clear()
    await friday_limiter.clear()
