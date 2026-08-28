"""Async SQLAlchemy database engine, session factory, and FastAPI dependencies."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from futuris.infra.config import settings

engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=(settings.LOG_LEVEL.upper() == "DEBUG"),
    future=True,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency yielding an async database session for request lifecycle."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
