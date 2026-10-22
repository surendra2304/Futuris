"""Regression tests for the model registry endpoint (bug C1: HTTP 500 AttributeError)."""

from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from futuris.api.app import app
from futuris.api.deps import get_db_session
from futuris.models.registry import model_registry
from futuris.storage.models import Base


@pytest_asyncio.fixture(scope="function")
async def isolated_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Client backed by an isolated in-memory database."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_models_returns_every_registered_adapter(isolated_client: httpx.AsyncClient):
    """GET /v1/models must not crash and must list each registered adapter once."""
    resp = await isolated_client.get("/v1/models")

    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert isinstance(items, list)
    assert len(items) == len(model_registry.list_models()) > 0

    names = [item["name"] for item in items]
    assert names == sorted(names)
    assert set(names) == set(model_registry.list_models())


@pytest.mark.asyncio
async def test_list_models_never_invents_benchmark_scores(isolated_client: httpx.AsyncClient):
    """Adapters without a persisted evaluation run report no benchmark numbers."""
    resp = await isolated_client.get("/v1/models")
    assert resp.status_code == 200

    for item in resp.json():
        assert item["benchmark_status"] == "not_benchmarked"
        assert item["benchmark_scores"] == {}
