"""Smoke test for health endpoint."""

from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from futuris import __version__
from futuris.api.app import app


@pytest.mark.asyncio
async def test_health_check(health_storage):
    """Verify GET /health returns status ok and correct version.

    ``health_storage`` is the point: status is ``ok`` only when the process-wide
    storage engine has its schema, so the test installs one instead of relying on
    whatever database happens to exist in the working directory.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == __version__


@pytest.mark.asyncio
async def test_health_declares_evidence_class_and_observation_time(health_storage):
    """The answer must name the class of evidence it carries and when it observed.

    A process that answers /health has proven it is up, not that any dependency
    is reachable, so the payload has to say which of those two it is.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    # The health answer is a liveness probe *plus* a storage probe: it says so,
    # and it names the storage measurement separately, because "the process
    # answered" does not imply "the database is usable".
    assert data["evidence_class"] == "process_liveness_plus_storage_probe"
    assert datetime.fromisoformat(data["observed_at"]).tzinfo is not None
    assert data["storage"]["expected"] >= 1
    assert isinstance(data["storage"]["missing"], list)


@pytest.mark.asyncio
async def test_health_answers_head_request(health_storage):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.head("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_reports_degraded_when_storage_is_not_ready(tmp_path, monkeypatch):
    """The counterpart contract: unusable storage must never be reported as ok.

    ``/health`` answers 200 either way -- the process is up -- but the verdict
    and the storage block must say what was measured. This is the half of the
    contract a clean checkout exercises: no schema, so ``degraded`` and the
    missing tables named.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    from futuris.storage import db as storage_db
    from futuris.storage.db import install_sqlite_pragmas

    empty = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'uninitialised.db'}")
    install_sqlite_pragmas(empty)
    monkeypatch.setattr(storage_db, "engine", empty)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["storage"]["ready"] is False
        assert data["storage"]["missing"], "the answer must name what is missing"
        assert data["storage"]["present"] == 0
    finally:
        await empty.dispose()
