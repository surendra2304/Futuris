"""Security regression tests for authentication boundaries (bug C4) and webhook safety (C5).

These tests pin the deliberate contract:
- reads used by the public dashboard stay anonymous-readable;
- every mutating/trigger endpoint rejects anonymous and invalid credentials;
- outbound webhooks reject non-HTTPS / internal targets;
- inbound research webhooks fail closed when unconfigured.
"""

from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from futuris.api.app import app
from futuris.api.deps import get_db_session
from futuris.infra.events import UnsafeWebhookUrlError, assert_safe_webhook_url
from futuris.storage.models import Base

MASTER_KEY = "auth_boundary_test_key_0123456789abcdef"


@pytest_asyncio.fixture(scope="function")
async def anon_client(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Client with no credentials; master key configured but never sent."""
    from futuris.infra.config import settings

    monkeypatch.setattr(settings, "FUTURIS_API_KEY", MASTER_KEY)
    monkeypatch.setattr(settings, "API_KEYS_ENABLED", True)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def _override() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_anonymous_reads_still_work(anon_client: httpx.AsyncClient):
    """The public dashboard must keep working without credentials."""
    for path in ("/v1/forecasts", "/v1/models", "/v1/predictions/matrix", "/v1/ecosystem/peers"):
        resp = await anon_client.get(path)
        assert resp.status_code == 200, f"{path}: {resp.status_code} {resp.text}"


@pytest.mark.asyncio
async def test_anonymous_mutations_are_rejected(anon_client: httpx.AsyncClient):
    """State-changing and compute-heavy endpoints must not accept anonymous callers."""
    cases = [
        ("post", "/v1/forecasts", {"target": "t", "horizon": "1h"}),
        ("post", "/v1/webhooks", {"url": "https://example.com/hook"}),
        ("delete", "/v1/webhooks/00000000-0000-0000-0000-000000000000", None),
        (
            "post",
            "/v1/predictions/predict",
            {"target": "sentinel:security:threat_anomaly_risk_24h"},
        ),
        ("post", "/v1/predictions/refresh-all", None),
        ("post", "/v1/market/forecast", {"symbol": "BTCUSDT"}),
        ("post", "/v1/ecosystem/seed", None),
        ("get", "/v1/events", None),
        ("get", "/v1/audit", None),
    ]
    for method, path, body in cases:
        resp = await getattr(anon_client, method)(
            path, **({"json": body} if body is not None else {})
        )
        assert resp.status_code in (401, 403), f"{method.upper()} {path} -> {resp.status_code}"
        assert resp.status_code != 200


@pytest.mark.asyncio
async def test_invalid_key_is_never_downgraded_to_anonymous(anon_client: httpx.AsyncClient):
    """A wrong key is a 401, not a silent anonymous read."""
    resp = await anon_client.get("/v1/events", headers={"X-API-Key": "not-a-real-key"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_authenticated_analyst_can_mutate(anon_client: httpx.AsyncClient):
    """With the master credential, the same endpoints work as before."""
    resp = await anon_client.post(
        "/v1/forecasts",
        json={"target": "service:checkout:capacity_exceedance_24h", "horizon": "1h"},
        headers={"X-API-Key": MASTER_KEY},
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_webhook_registration_rejects_non_public_targets(anon_client: httpx.AsyncClient):
    """Internal/loopback and non-HTTPS webhook targets must be refused."""
    headers = {"X-API-Key": MASTER_KEY}
    for url in ("http://example.com/hook", "https://127.0.0.1/hook", "https://localhost/hook"):
        resp = await anon_client.post("/v1/webhooks", json={"url": url}, headers=headers)
        assert resp.status_code == 422, f"{url} -> {resp.status_code}"


def test_webhook_url_validator_rejects_credentials_and_internal_hosts():
    for bad in (
        "https://user:pass@example.com/hook",
        "http://example.com/hook",
        "https://10.0.0.5/hook",
        "https://169.254.169.254/latest/meta-data",
        "https://metadata.google.internal/x",
    ):
        with pytest.raises(UnsafeWebhookUrlError):
            assert_safe_webhook_url(bad)
    assert_safe_webhook_url("https://example.com/hook")


@pytest.mark.asyncio
async def test_inbound_research_webhook_fails_closed_when_unconfigured(
    anon_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """With no shared secret configured, inbound deliveries are refused (503)."""
    for var in ("INTELX_WEBHOOK_API_KEY", "FUTURIS_FRIDAY_API_KEY", "FUTURIS_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    from futuris.infra.config import settings

    monkeypatch.setattr(settings, "FUTURIS_API_KEY", None)
    monkeypatch.setattr(settings, "FUTURIS_FRIDAY_API_KEY", None)

    resp = await anon_client.post(
        "/v1/webhooks/research-finding-relevant",
        json={
            "event": "research_finding_relevant",
            "data": {"run_id": "r1", "finding_summary": "x"},
        },
    )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_inbound_research_webhook_requires_the_shared_secret(
    anon_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    secret = "intelx_webhook_secret_0123456789abcdef"
    monkeypatch.setenv("INTELX_WEBHOOK_API_KEY", secret)

    payload = {
        "event": "research_finding_relevant",
        "data": {"run_id": "r2", "finding_summary": "catalyst", "recommended_forecast_targets": []},
    }
    bad = await anon_client.post("/v1/webhooks/research-finding-relevant", json=payload)
    assert bad.status_code == 401

    # Correct secret is accepted (durable store failure would be 503, not 401).
    ok = await anon_client.post(
        "/v1/webhooks/research-finding-relevant", json=payload, headers={"X-API-Key": secret}
    )
    assert ok.status_code in (200, 503), ok.text
    if ok.status_code == 503:
        # Memora is not reachable in CI; the point is that auth was accepted.
        assert "durably stored" in ok.text
