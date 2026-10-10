"""Demo seeding is heavyweight and must not be stackable.

Found under 8-way concurrent load (``scripts/adversarial_harness.py``): every
``POST /v1/ecosystem/seed`` queued another full 180-day re-seed as a background
task, each running in one long-lived write transaction.  That starved unrelated
writers and produced ``503 storage_busy`` on ``POST /v1/predictions/predict`` --
a write endpoint with nothing to do with seeding.

These tests pin the guard: one seed at a time, later triggers answered rather
than queued, and the slot released even when the seed fails.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio

from futuris.api.app import app
from futuris.api.routers import ecosystem as ecosystem_router
from futuris.infra.config import settings

ADMIN_KEY = "demo_seed_admin_key_0123456789abcdef"


@pytest_asyncio.fixture(autouse=True)
def reset_guard() -> None:
    ecosystem_router.seed_guard._active = False  # noqa: SLF001 - reset shared state
    ecosystem_router.seed_guard.completed_runs = 0
    yield
    ecosystem_router.seed_guard._active = False  # noqa: SLF001


@pytest_asyncio.fixture
async def client(
    monkeypatch: pytest.MonkeyPatch, ready_storage
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Admin client over an isolated database.

    The seed route writes an audit row, so the client must not share the workspace
    database with a running server (its writes produced intermittent 503
    ``storage_busy`` responses, which broke the status checks in this test).
    """
    monkeypatch.setattr(settings, "FUTURIS_API_KEY", ADMIN_KEY)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers={"X-API-Key": ADMIN_KEY}
    ) as http_client:
        yield http_client


@pytest.mark.asyncio
async def test_concurrent_triggers_start_exactly_one_seed(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Six simultaneous triggers: one accepted, five told it is already running.

    The fake seeder sleeps briefly rather than blocking on an event: a Starlette
    response is not complete until its background task finishes, so a blocking
    seeder would deadlock the test rather than the guard.
    """
    runs = 0

    class SlowSeeder:
        def __init__(self, seed: int = 42) -> None:
            self.seed = seed

        async def run(self) -> dict[str, object]:
            nonlocal runs
            runs += 1
            await asyncio.sleep(0.4)
            return {"status": "seeded"}

    monkeypatch.setattr(ecosystem_router, "DemoSeeder", SlowSeeder)

    responses = await asyncio.gather(*(client.post("/v1/ecosystem/seed") for _ in range(6)))
    statuses = [response.json()["status"] for response in responses]

    assert statuses.count("accepted") == 1, statuses
    assert statuses.count("already_running") == 5, statuses
    assert runs == 1, "only the accepted trigger may run a seed"
    assert ecosystem_router.seed_guard.completed_runs == 1
    assert ecosystem_router.seed_guard.running is False, "the slot must be released"

    await asyncio.sleep(0.05)
    again = await client.post("/v1/ecosystem/seed")
    assert again.json()["status"] == "accepted", "gating concurrency must not ban seeding"


@pytest.mark.asyncio
async def test_seed_slot_is_released_when_a_run_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crashed seed must not lock the workspace out of seeding forever."""

    class ExplodingSeeder:
        def __init__(self, seed: int = 42) -> None:
            self.seed = seed

        async def run(self) -> dict[str, object]:
            msg = "synthetic telemetry generator unavailable"
            raise RuntimeError(msg)

    monkeypatch.setattr(ecosystem_router, "DemoSeeder", ExplodingSeeder)

    assert ecosystem_router.seed_guard.claim() is True
    await ecosystem_router.seed_guard.run_once(lambda: ExplodingSeeder())

    assert ecosystem_router.seed_guard.running is False
    assert ecosystem_router.seed_guard.completed_runs == 0
    assert "RuntimeError" in (ecosystem_router.seed_guard.last_error or "")


@pytest.mark.asyncio
async def test_trigger_reports_when_a_seed_is_already_running(
    client: httpx.AsyncClient,
) -> None:
    """The refusal is explicit (status + since when), not a silent queue."""
    assert ecosystem_router.seed_guard.claim() is True
    ecosystem_router.seed_guard.started_at = datetime.now(UTC)
    try:
        response = await client.post("/v1/ecosystem/seed")
    finally:
        ecosystem_router.seed_guard._active = False  # noqa: SLF001

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "already_running"
    assert body["started_at"] is not None
    assert "already in progress" in body["message"]
    assert body["completed_runs"] == 0
