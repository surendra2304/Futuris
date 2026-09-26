from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import httpx

from futuris.integrations.memora_event_consumer import (
    CONSUMER_ID,
    consume_memora_events_once,
    _persist_notice_to_memora,
)


class FakeMemora:
    def __init__(self, events, order=None):
        self.events = events
        self.acks = []
        self.order = order

    def read_event_cursor(self, agent, consumer_id):
        assert agent == "futuris"
        assert consumer_id == CONSUMER_ID
        return {"status": "ok", "after_id": 0}

    def poll_events(self, agent, after_id, limit):
        assert agent == "futuris"
        assert after_id == 0
        return {"status": "ok", "events": self.events}

    def acknowledge_event(self, agent, event_id, consumer_id):
        self.acks.append(event_id)
        if self.order is not None:
            self.order.append("ack")
        return {"status": "ok", "after_id": event_id}


class FakeSession:
    def __init__(self, rows, fail_commit=False, order=None):
        self.rows = rows
        self.fail_commit = fail_commit
        self.pending = []
        self.order = order

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, _model, key):
        return self.rows.get(key)

    def add(self, row):
        self.pending.append(row)

    async def commit(self):
        if self.fail_commit:
            raise RuntimeError("database unavailable")
        if self.order is not None:
            self.order.append("local_commit")
        for row in self.pending:
            self.rows[row.event_id] = row


@pytest.mark.asyncio
async def test_intelx_notice_is_saved_before_ack_and_duplicate_is_idempotent(monkeypatch):
    order = []
    rows = {}
    fake_client = FakeMemora([
        {
            "id": 4,
            "event_id": "intelx-run-4-all",
            "event_type": "intelx.news",
            "payload": {"headline": "Exchange notice", "source_agent": "intelx"},
        }
    ], order=order)

    async def persist_notice(_event, _payload):
        order.append("memora_commit")

    monkeypatch.setattr("futuris.integrations.memora_event_consumer._persist_notice_to_memora", persist_notice)

    @asynccontextmanager
    async def sessions():
        yield FakeSession(rows, order=order)

    monkeypatch.setattr("futuris.integrations.memora_event_consumer.async_session_factory", sessions)
    assert await consume_memora_events_once(fake_client) == 1
    assert list(rows) == ["intelx-run-4-all"]
    assert fake_client.acks == [4]
    assert order == ["memora_commit", "local_commit", "ack"]

    # A replay after a failed ack/pod restart finds the inbox row and only retries ack.
    assert await consume_memora_events_once(fake_client) == 1
    assert len(rows) == 1
    assert fake_client.acks == [4, 4]


@pytest.mark.asyncio
async def test_persistence_failure_does_not_ack_or_advance(monkeypatch):
    rows = {}
    memora_writes = []
    fake_client = FakeMemora([
        {
            "id": 5,
            "event_id": "intelx-run-5-all",
            "event_type": "intelx.news",
            "payload": {"headline": "Exchange notice"},
        }
    ])

    async def persist_notice(_event, _payload):
        memora_writes.append("persisted")

    monkeypatch.setattr("futuris.integrations.memora_event_consumer._persist_notice_to_memora", persist_notice)

    @asynccontextmanager
    async def sessions():
        yield FakeSession(rows, fail_commit=True)

    monkeypatch.setattr("futuris.integrations.memora_event_consumer.async_session_factory", sessions)
    with pytest.raises(RuntimeError, match="database unavailable"):
        await consume_memora_events_once(fake_client)
    assert memora_writes == ["persisted"]
    assert fake_client.acks == []


@pytest.mark.asyncio
async def test_other_event_is_skipped_but_acknowledged_in_order(monkeypatch):
    fake_client = FakeMemora([
        {"id": 6, "event_id": "evt-6", "event_type": "memory.created", "payload": {}}
    ])

    @asynccontextmanager
    async def sessions():
        yield FakeSession({})

    monkeypatch.setattr("futuris.integrations.memora_event_consumer.async_session_factory", sessions)
    assert await consume_memora_events_once(fake_client) == 1
    assert fake_client.acks == [6]


@pytest.mark.asyncio
async def test_intelx_notice_is_written_to_memora_with_stable_idempotency(monkeypatch):
    captured = {}

    async def handler(request):
        captured["body"] = request.read()
        captured["authorization"] = request.headers.get("Authorization")
        return httpx.Response(201, json={"id": "memora-record-1"})

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "futuris.integrations.memora_event_consumer.settings.MEMORA_API_KEY",
        "futuris-memora-key",
    )
    monkeypatch.setattr(
        "futuris.integrations.memora_event_consumer.settings.MEMORA_URL",
        "https://memora.invalid",
    )
    monkeypatch.setattr(
        "futuris.integrations.memora_event_consumer.httpx.AsyncClient",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )
    event = {"id": 7, "event_id": "intelx-stable-7", "event_type": "intelx.news"}
    payload = {
        "headline": "Exchange filing",
        "summary": "The exchange published a filing.",
        "source_url": "https://example.invalid/filing",
        "source_agent": "intelx",
    }

    await _persist_notice_to_memora(event, payload)
    import json

    body = json.loads(captured["body"])
    assert captured["authorization"] == "Bearer futuris-memora-key"
    assert body["idempotency_key"].startswith("futuris-intelx-")
    assert body["target_namespace_path"] == "memora://futuris/intelx/notices"
    assert body["provenance"]["event_id"] == "intelx-stable-7"
    assert body["confidence"] == 0.0
