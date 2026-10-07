"""Shared test fixtures.

Fixtures here exist because a test must control the state it asserts on. The
first version of the health tests did not, and passed on a developer machine
(where ``./data/futuris.db`` already existed from earlier runs) while failing on
a clean CI checkout -- ``/health`` reported ``degraded`` because the process-wide
storage engine had no schema. That is a test bug, not an app bug.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from futuris.storage import db as storage_db
from futuris.storage.db import install_sqlite_pragmas
from futuris.storage.models import Base


@pytest_asyncio.fixture
async def ready_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[AsyncEngine, None]:
    """Point the process-wide storage at a temporary, schema-initialised database.

    ``/health`` measures the *real* engine (``futuris.storage.db.engine``), not
    the request-scoped session a test may have overridden, so a test asserting a
    healthy storage verdict has to control that engine explicitly. The database
    is a throwaway file with the full schema, installed exactly like production
    (``install_sqlite_pragmas``), and the original engine is restored afterwards.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'storage_probe.db'}")
    install_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

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
