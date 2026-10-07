import json
from datetime import UTC, datetime

import httpx
import pytest

from futuris.ecosystem.adapters import EcosystemAdapter, MemoraMemoryCandidate
from futuris.infra.config import Settings


def test_futuris_named_key_is_selected_for_memora(monkeypatch):
    monkeypatch.delenv("MEMORA_API_KEY", raising=False)
    config = Settings(_env_file=None, FUTURIS_API_KEY="futuris-agent-key")
    assert config.MEMORA_API_KEY == "futuris-agent-key"


@pytest.mark.asyncio
async def test_futuris_memora_write_uses_named_key_and_requires_created_receipt(monkeypatch):
    captured = {}
    monkeypatch.setattr("futuris.ecosystem.adapters.settings.MEMORA_API_KEY", "futuris-agent-key")
    monkeypatch.setattr("futuris.ecosystem.adapters.settings.MEMORA_URL", "https://memora.invalid")

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.read().decode())
        return httpx.Response(201, json={"id": "memora-record-1"})

    original = httpx.AsyncClient

    class Client:
        async def __aenter__(self):
            self.client = original(transport=httpx.MockTransport(handler))
            await self.client.__aenter__()
            return self.client

        async def __aexit__(self, *args):
            return await self.client.__aexit__(*args)

    monkeypatch.setattr("futuris.ecosystem.adapters.httpx.AsyncClient", lambda **_kwargs: Client())
    candidate = MemoraMemoryCandidate(
        "c-1", "market:BTC", "forecast evidence", {}, datetime.now(UTC)
    )
    assert await EcosystemAdapter().publish_memora_candidate(candidate) is True
    assert captured["url"] == "https://memora.invalid/v1/memories"
    assert captured["authorization"] == "Bearer futuris-agent-key"
    assert captured["payload"]["target_namespace_path"] == "memora://futuris/forecasts"


@pytest.mark.asyncio
async def test_futuris_missing_named_memora_key_fails_closed(monkeypatch):
    monkeypatch.setattr("futuris.ecosystem.adapters.settings.MEMORA_API_KEY", None)
    candidate = MemoraMemoryCandidate(
        "c-2", "market:BTC", "forecast evidence", {}, datetime.now(UTC)
    )
    assert await EcosystemAdapter().publish_memora_candidate(candidate) is False
