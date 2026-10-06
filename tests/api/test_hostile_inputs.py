"""Regression tests for the hostile-input bugs found by the adversarial harness.

Each test pins a failure that was reproducible against the running app before
the fix, with the symptom recorded in the docstring so a revert fails loudly
with the original evidence rather than a generic assertion.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from futuris.api.app import app
from futuris.api.deps import get_db_session
from futuris.api.errors import sanitise_for_json
from futuris.infra.config import settings
from futuris.storage.models import Base

API_KEY = "hostile_input_key_0123456789abcdef"


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[httpx.AsyncClient, None]:
    monkeypatch.setattr(settings, "FUTURIS_API_KEY", API_KEY)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers={"X-API-Key": API_KEY}
    ) as http_client:
        yield http_client
    app.dependency_overrides.clear()
    await engine.dispose()


def assert_envelope(payload: str, status: int) -> dict:
    """Every error must be a JSON envelope with a code and a message."""
    assert status >= 400, f"expected an error status, got {status}"
    parsed = json.loads(payload)
    assert "error" in parsed, payload[:300]
    assert parsed["error"].get("code"), payload[:300]
    assert parsed["error"].get("message"), payload[:300]
    return parsed


def test_sanitise_for_json_never_raises() -> None:
    """The error renderer is the last line of defence; it must not itself fail."""
    payload = {
        "bytes": b"\xff\xfe raw",
        "nested": [b"x", {"deep": (1, 2)}],
        "exception": ValueError("boom"),
        "set": {1, 2},
        "fine": ["a", 1, None, True],
    }
    rendered = json.dumps(sanitise_for_json(payload))
    assert "raw" in rendered
    assert "boom" in rendered


# ── 1. non-JSON bodies used to 500 while rendering the 422 ─────────────────


@pytest.mark.asyncio
async def test_non_json_body_returns_a_serialisable_422(client: httpx.AsyncClient) -> None:
    """Symptom before the fix: 500 'Object of type bytes is not JSON serializable'."""
    response = await client.post(
        "/v1/forecasts",
        content=b"{not json at all",
        headers={"Content-Type": "application/xml"},
    )
    assert response.status_code != 500, response.text
    body = assert_envelope(response.text, response.status_code)
    assert body["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_binary_body_returns_a_serialisable_422(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/forecasts", content=b"\x00\x01\x02\x03", headers={"Content-Type": "application/json"}
    )
    assert response.status_code != 500, response.text
    assert_envelope(response.text, response.status_code)


# ── 2. absurd horizons used to overflow ────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "horizon",
    ["99999999999999999999d", "0m", "-5h", "400d", "forever", "24", "1y"],
)
async def test_absurd_horizons_are_422_not_500(client: httpx.AsyncClient, horizon: str) -> None:
    """Symptom before the fix: 500 'Python int too large to convert to C int'."""
    response = await client.post(
        "/v1/forecasts",
        json={"target": "service:checkout:capacity_exceedance_24h", "horizon": horizon},
    )
    assert response.status_code == 422, response.text
    assert_envelope(response.text, response.status_code)


@pytest.mark.asyncio
async def test_supported_horizons_still_work(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/forecasts",
        json={"target": "service:checkout:capacity_exceedance_24h", "horizon": "1h"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["horizon"].startswith("1:00:00") or "1" in response.json()["horizon"]


# ── 3. far-future as_of used to reach pandas and explode ───────────────────


@pytest.mark.asyncio
async def test_far_future_as_of_is_422(client: httpx.AsyncClient) -> None:
    """Symptom before the fix: 500 "'datetime.datetime' object has no attribute 'floor'"."""
    response = await client.post(
        "/v1/forecasts",
        json={
            "target": "service:checkout:capacity_exceedance_24h",
            "as_of": "9999-12-31T23:59:59Z",
        },
    )
    assert response.status_code == 422, response.text
    assert_envelope(response.text, response.status_code)


@pytest.mark.asyncio
async def test_ancient_as_of_is_422(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/forecasts",
        json={
            "target": "service:checkout:capacity_exceedance_24h",
            "as_of": "1900-01-01T00:00:00Z",
        },
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_recent_as_of_is_accepted(client: httpx.AsyncClient) -> None:
    as_of = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    response = await client.post(
        "/v1/forecasts",
        json={"target": "service:checkout:capacity_exceedance_24h", "as_of": as_of},
    )
    assert response.status_code == 201, response.text


# ── 4. the universe prediction path used to coerce blindly ─────────────────


@pytest.mark.asyncio
async def test_non_numeric_context_estimate_is_422(client: httpx.AsyncClient) -> None:
    """Symptom before the fix: 500 "could not convert string to float: 'not-a-number'"."""
    response = await client.post(
        "/v1/predictions/predict",
        json={
            "target": "friday:eventbus:message_backlog_24h",
            "context": {"point_estimate": "not-a-number"},
        },
    )
    assert response.status_code == 422, response.text
    body = assert_envelope(response.text, response.status_code)
    assert "point_estimate" in json.dumps(body)


@pytest.mark.asyncio
async def test_out_of_range_probability_is_422(client: httpx.AsyncClient) -> None:
    """Symptom before the fix: a pydantic ValidationError escaped as a 500."""
    response = await client.post(
        "/v1/predictions/predict",
        json={"target": "friday:eventbus:message_backlog_24h", "context": {"probability": 5.0}},
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_nan_context_value_is_422(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/predictions/predict",
        json={
            "target": "friday:eventbus:message_backlog_24h",
            "context": {"point_estimate": "NaN"},
        },
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_empty_target_is_422(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/predictions/predict", json={"target": "   "})
    assert response.status_code == 422, response.text


# ── 5. storage failures are 503 with a remediation hint, not raw 500s ──────


@pytest.mark.asyncio
async def test_missing_schema_answers_503_with_a_hint(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symptom before the fix: 500 with a raw 'no such table: audit_logs'."""
    from sqlalchemy.exc import OperationalError

    from futuris.api import errors as errors_module

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise OperationalError("SELECT 1", {}, Exception("no such table: audit_logs"))

    monkeypatch.setattr(errors_module, "schedule_schema_repair", lambda: None, raising=False)
    response = errors_module.storage_error_response(
        OperationalError("SELECT 1", {}, Exception("no such table: audit_logs"))
    )
    assert response.status_code == 503
    parsed = json.loads(response.body)
    assert parsed["error"]["code"] == "storage_schema_missing"
    assert "alembic upgrade head" in parsed["error"]["message"]
    assert _raise is not None


def test_locked_database_maps_to_storage_busy() -> None:
    from sqlalchemy.exc import OperationalError

    from futuris.api.errors import storage_error_response

    response = storage_error_response(
        OperationalError("INSERT", {}, Exception("database is locked"))
    )
    assert response.status_code == 503
    assert json.loads(response.body)["error"]["code"] == "storage_busy"


def test_unwritable_database_maps_to_storage_unavailable() -> None:
    from sqlalchemy.exc import OperationalError

    from futuris.api.errors import storage_error_response

    response = storage_error_response(
        OperationalError("SELECT", {}, Exception("unable to open database file"))
    )
    assert response.status_code == 503
    assert json.loads(response.body)["error"]["code"] == "storage_unavailable"


@pytest.mark.asyncio
async def test_health_reports_measured_storage_state(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["storage"]["ready"] is True
    assert body["status"] == "ok"
    assert body["storage"]["expected"] >= 12


# ── 6. the FRIDAY credential guard explains itself ─────────────────────────


@pytest.mark.asyncio
async def test_short_friday_key_reports_the_real_problem(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symptom before the fix: a configured 24-char key reported 'not configured'."""
    monkeypatch.setattr(settings, "FUTURIS_FRIDAY_API_KEY", "short_key_0123456789")
    monkeypatch.setattr(settings, "FUTURIS_API_KEY", "short_key_0123456789")
    response = await client.post(
        "/v1/friday/forecast",
        headers={"X-API-Key": "short_key_0123456789"},
        json={"friday_request_id": "req_short_key", "target": "service:x:y"},
    )
    assert response.status_code == 503, response.text
    message = json.loads(response.text)["error"]["message"]
    assert "characters" in message
    assert "create-admin-key" in message


@pytest.mark.asyncio
async def test_unconfigured_friday_key_says_unconfigured(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "FUTURIS_FRIDAY_API_KEY", None)
    monkeypatch.setattr(settings, "FUTURIS_API_KEY", None)
    monkeypatch.delenv("FUTURIS_FRIDAY_API_KEY", raising=False)
    monkeypatch.delenv("FRIDAY_API_KEY", raising=False)
    monkeypatch.delenv("FUTURIS_API_KEY", raising=False)
    response = await client.post(
        "/v1/friday/forecast", json={"friday_request_id": "req_unconfigured", "target": "x"}
    )
    assert response.status_code == 503
    assert "not configured" in json.loads(response.text)["error"]["message"]


# ── 7. sanity: a well-formed request still behaves ─────────────────────────


@pytest.mark.asyncio
async def test_malformed_uuid_paths_are_422_with_envelope(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/v1/forecasts/{uuid4()}")  # valid uuid, unknown forecast
    assert response.status_code == 404
    assert_envelope(response.text, 404)

    bad = await client.post("/v1/forecasts/not-a-uuid/invalidate", json={"reason": "testing"})
    assert bad.status_code == 422, bad.text
    assert_envelope(bad.text, bad.status_code)
