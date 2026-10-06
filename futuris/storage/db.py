"""Async SQLAlchemy database engine, session factory, and FastAPI dependencies."""

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import event as sa_event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from futuris.infra.config import settings
from futuris.infra.logging import get_logger

logger = get_logger("futuris.storage.db")


def sqlite_file_path(database_url: str) -> Path | None:
    """Return the on-disk path of a SQLite URL, or None for other dialects."""
    url = make_url(database_url)
    if not url.get_backend_name().startswith("sqlite"):
        return None
    database = url.database
    if not database or database == ":memory:":
        return None
    return Path(database)


def ensure_storage_directories(
    database_url: str | None = None, object_store_path: str | None = None
) -> None:
    """Create the directories the application needs before anything opens them.

    ``data/`` is intentionally not committed, so a fresh checkout has no
    directory for the default SQLite file and every connection fails with
    "unable to open database file" -- including the startup schema check. The
    application creates what it needs instead of requiring a manual mkdir.
    """
    db_path = sqlite_file_path(database_url or settings.DATABASE_URL)
    if db_path is not None and str(db_path.parent) not in {"", "."}:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug("storage_directory_ready", path=str(db_path.parent))
    store = Path(object_store_path or settings.OBJECT_STORE_PATH)
    store.mkdir(parents=True, exist_ok=True)


ensure_storage_directories()

engine_kwargs = {
    "echo": (settings.LOG_LEVEL.upper() == "DEBUG"),
    "future": True,
}
if "sqlite" in settings.DATABASE_URL:
    # ``timeout`` is sqlite3's busy timeout: without it a concurrent writer gets
    # an immediate "database is locked" instead of waiting its turn. Measured
    # under an 8-way concurrent fuzz run, this was the difference between 503s
    # from the storage-error mapper and no storage errors at all.
    engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30.0}
else:
    engine_kwargs["pool_pre_ping"] = True

engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    **engine_kwargs,
)

def install_sqlite_pragmas(target_engine: AsyncEngine) -> None:
    """Install the WAL / busy-timeout / foreign-key PRAGMAs on a SQLite engine.

    Exposed so tests can build a throwaway engine that behaves exactly like the
    production one -- in particular ``PRAGMA foreign_keys=ON``, which is what
    makes the flush-ordering contract in ``futuris/storage/models.py`` real
    instead of decorative.
    """

    @sa_event.listens_for(target_engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection: object, _record: object) -> None:
        """WAL + a generous busy timeout for the file-backed SQLite deployment.

        WAL lets readers proceed while a writer commits, which is what the
        dashboard, the scheduler and a forecast request do simultaneously.
        """
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


if "sqlite" in settings.DATABASE_URL:
    install_sqlite_pragmas(engine)


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


def _sqlite_add_column_ddl(table_name: str, column: object, dialect: object) -> str | None:
    """DDL for one missing column, or None when it cannot be added safely.

    SQLite only accepts ``ADD COLUMN`` when the new column is nullable or has a
    constant default, so anything else is reported instead of guessed at.
    """
    from sqlalchemy import Column

    assert isinstance(column, Column)
    type_sql = column.type.compile(dialect=dialect)
    ddl = f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {type_sql}'
    if column.server_default is not None:
        ddl += f" DEFAULT {column.server_default.arg}"
    elif not column.nullable:
        default = column.default
        argument = getattr(default, "arg", None)
        if default is None or callable(argument):
            return None
        literal = f"'{argument}'" if isinstance(argument, str) else str(argument)
        ddl += f" DEFAULT {literal}"
    return ddl


async def add_missing_columns(target_engine: AsyncEngine | None = None) -> list[str]:
    """Add ORM columns a pre-existing SQLite database has not got yet.

    ``create_all`` only ever creates whole tables, so a database written by an
    earlier release keeps the old shape while the ORM moves on -- exactly the
    drift that Alembic migrations carry for PostgreSQL.  Without this a new
    column turns every read into ``no such column`` until an operator
    intervenes; with it the process heals itself on startup.
    """
    from sqlalchemy.schema import CreateIndex

    from futuris.storage.models import Base

    active_engine = target_engine or engine
    added: list[str] = []
    skipped: list[str] = []
    created_indexes: list[str] = []
    try:
        async with active_engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                rows = (
                    await conn.exec_driver_sql(f"PRAGMA table_info('{table.name}')")
                ).fetchall()
                if not rows:
                    continue  # table missing entirely: create_all owns that
                present = {row[1] for row in rows}
                for column in table.columns:
                    if column.name in present:
                        continue
                    ddl = _sqlite_add_column_ddl(table.name, column, active_engine.dialect)
                    if ddl is None:
                        skipped.append(f"{table.name}.{column.name}")
                        continue
                    await conn.exec_driver_sql(ddl)
                    added.append(f"{table.name}.{column.name}")
                if not table.indexes:
                    continue
                present_indexes = {
                    row[1]
                    for row in (
                        await conn.exec_driver_sql(f"PRAGMA index_list('{table.name}')")
                    ).fetchall()
                }
                for index in table.indexes:
                    if index.name in present_indexes:
                        continue
                    try:
                        await conn.exec_driver_sql(
                            str(CreateIndex(index).compile(dialect=active_engine.dialect))
                        )
                        created_indexes.append(index.name)
                    except Exception as exc:  # noqa: BLE001 - per-index best effort
                        logger.warning(
                            "schema_index_repair_failed", index=index.name, error=str(exc)[:200]
                        )
    except Exception as exc:  # noqa: BLE001 - repair must never block startup
        logger.warning("schema_column_repair_failed", error=f"{type(exc).__name__}: {exc}")
        return added
    if added:
        logger.warning("schema_columns_added", columns=added)
    if created_indexes:
        logger.warning("schema_indexes_added", indexes=created_indexes)
    if skipped:
        logger.error("schema_columns_not_addable", columns=skipped)
    return added


async def ensure_schema() -> dict[str, object]:
    """Create any missing tables and verify the result.

    Only used for SQLite. On PostgreSQL (and any other dialect) Alembic owns the
    schema, so this refuses to create tables and simply reports the measurement.
    """
    if not engine.dialect.name.startswith("sqlite"):
        return await verify_schema()

    from futuris.storage.models import Base

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # noqa: BLE001
        logger.warning("schema_create_failed", error=f"{type(exc).__name__}: {exc}")
    await add_missing_columns()
    report = await verify_schema()
    if report["ready"]:
        logger.info("schema_verified", tables=report["present"])
    else:
        logger.error("schema_incomplete", missing=report["missing"], error=report["error"])
    return report


_repair_task: asyncio.Task | None = None


def schedule_schema_repair() -> None:
    """Queue one bounded schema repair for the running loop (SQLite only).

    Called when a request discovers the schema is missing -- for example an ASGI
    host that skipped the lifespan hook. At most one repair runs at a time, so a
    burst of failing requests cannot stampede ``create_all``.
    """
    global _repair_task
    if not engine.dialect.name.startswith("sqlite"):
        return
    if _repair_task is not None and not _repair_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _repair_task = loop.create_task(ensure_schema())
