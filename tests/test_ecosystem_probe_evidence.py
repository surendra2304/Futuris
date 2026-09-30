from datetime import datetime

import pytest

from futuris.ecosystem.adapters import EcosystemAdapter


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class FakeAsyncClient:
    calls = 0

    def __init__(self, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, *_args, **_kwargs):
        type(self).calls += 1
        if type(self).calls == 2:
            return FakeResponse(503)
        if type(self).calls == 3:
            raise TimeoutError("probe timed out")
        return FakeResponse(200)


@pytest.mark.asyncio
async def test_peer_probe_reports_http_evidence_and_server_observation(monkeypatch):
    FakeAsyncClient.calls = 0
    monkeypatch.setattr("futuris.ecosystem.adapters.httpx.AsyncClient", FakeAsyncClient)

    peers = await EcosystemAdapter().probe_peers()

    assert len(peers) == 8
    assert peers[0]["status"] == "online"
    assert peers[0]["evidence_class"] == "http_health_200"
    assert peers[0]["http_status"] == 200
    assert datetime.fromisoformat(peers[0]["observed_at"]).utcoffset() is not None

    assert peers[1]["status"] == "offline"
    assert peers[1]["evidence_class"] == "http_health_non_200"
    assert peers[1]["http_status"] == 503

    assert peers[2]["status"] == "offline"
    assert peers[2]["evidence_class"] == "probe_failed"
    assert peers[2]["http_status"] is None
