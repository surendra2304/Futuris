"""Async SQLAlchemy engine, session factory, and FastAPI dependencies.

The only supported store is the Oracle Autonomous Database. There is no local
file database and no PostgreSQL path: ``DATABASE_URL`` must name an
``oracle+oracledb://`` DSN and configuration fails closed when it does not.
"""

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from futuris.infra.config import settings
from futuris.infra.logging import get_logger

logger = get_logger("futuris.storage.db")

DATABASE_URL: str = settings.resolve_database_url()


def ensure_storage_directories(object_store_path: str | None = None) -> None:
    """Create the directories the application needs before anything opens them.

    Only the object/evidence store is a local directory now; the relational
    store lives in Oracle and never touches the filesystem.
    """
    store = Path(object_store_path or settings.OBJECT_STORE_PATH)
    store.mkdir(parents=True, exist_ok=True)


ensure_storage_directories()

engine_kwargs: dict[str, object] = {
    "echo": (settings.LOG_LEVEL.upper() == "DEBUG"),
    "future": True,
    # Oracle: size the pool for a burst of concurrent forecast requests (each
    # holds a session across its pipeline run), recycle stale connections and
    # fail fast with a ping when a connection sat idle too long. Pool checkout
    # timeouts are mapped to 503 by the API error layer.
    "pool_size": 10,
    "max_overflow": 20,
    "pool_timeout": 15,
    "pool_recycle": 1800,
    "pool_pre_ping": True,
}

_effective_db_url = DATABASE_URL
if _effective_db_url.startswith("oracle+oracledb://"):
    _effective_db_url = "oracle+oracledb_async://" + _effective_db_url[len("oracle+oracledb://"):]

engine: AsyncEngine = create_async_engine(
    _effective_db_url,
    **engine_kwargs,
)


async def checkpoint_and_close_db() -> None:
    """Dispose database handles cleanly on shutdown."""
    try:
        await engine.dispose()
        logger.info("database_checkpoint_and_close_success")
    except Exception as exc:
        logger.warning("database_checkpoint_failed", error=str(exc))


async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def safe_rollback(session: AsyncSession) -> None:
    """Roll a session back without letting a teardown failure escape.

    ``Session.rollback`` can itself raise (a closed or broken DBAPI connection,
    a failed flush that left the transaction unusable).  Teardown runs *after*
    the response has been produced, so an exception raised there cannot be
    turned into an error envelope any more -- it only aborts the connection.
    """
    try:
        await session.rollback()
    except Exception as exc:  # noqa: BLE001 - teardown must never raise
        logger.warning("session_rollback_failed", error=type(exc).__name__)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency yielding an async database session for request lifecycle."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await safe_rollback(session)
            raise
        finally:
            await session.close()


async def verify_schema() -> dict[str, object]:
    """Report whether every table the ORM declares exists.

    Startup uses this instead of trusting ``create_all``: a read-only or locked
    database can accept the DDL call and still have no usable schema, and the
    process must know that before it starts answering requests.
    """
    from sqlalchemy import inspect as sa_inspect

    from futuris.storage.models import Base

    expected = {table.name for table in Base.metadata.sorted_tables}

    def _present(sync_conn: object) -> set[str]:
        inspector = sa_inspect(sync_conn)
        return set(inspector.get_table_names())

    try:
        async with engine.connect() as conn:
            present = await conn.run_sync(_present)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return {
            "ready": False,
            "expected": len(expected),
            "present": 0,
            "missing": sorted(expected),
            "error": f"{type(exc).__name__}: {exc}",
        }

    missing = sorted(expected - present)
    return {
        "ready": not missing,
        "expected": len(expected),
        "present": len(present & expected),
        "missing": missing,
        "error": None,
    }


async def ensure_schema() -> dict[str, object]:
    """Create any missing tables in the Oracle schema and verify the result.

    Oracle is bootstrapped from SQLAlchemy metadata because a freshly
    provisioned FUTURIS schema starts empty. ``create_all`` is idempotent and
    only creates tables that are absent.
    """
    from futuris.storage.models import Base

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001
        logger.warning("schema_create_failed", error=f"{type(exc).__name__}: {exc}")
    report = await verify_schema()
    if report["ready"]:
        logger.info("schema_verified", tables=report["present"])
    else:
        logger.error("schema_incomplete", missing=report["missing"], error=report["error"])
    return report


_repair_task: asyncio.Task | None = None


def schedule_schema_repair() -> None:
    """Queue one bounded schema repair for the running loop.

    Called when a request discovers the schema is missing -- for example an ASGI
    host that skipped the lifespan hook. At most one repair runs at a time, so a
    burst of failing requests cannot stampede ``create_all``.
    """
    global _repair_task
    if _repair_task is not None and not _repair_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _repair_task = loop.create_task(ensure_schema())
