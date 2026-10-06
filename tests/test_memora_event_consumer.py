from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest

from futuris.api.routers.webhooks import (
    IntelXResearchWebhookPayload,
    ResearchFindingEventData,
    _store_webhook_notice,
    _symbol_from_explicit_targets,
)
from futuris.integrations.memora_event_consumer import (
    CONSUMER_ID,
    _persist_notice_to_memora,
    consume_memora_events_once,
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
        assert limit == 100
        return {"status": "ok", "events": self.events}

    def acknowledge_event(self, agent, event_id, consumer_id):
        assert agent == "futuris"
        assert consumer_id == CONSUMER_ID
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
    fake_client = FakeMemora(
        [
            {
                "id": 4,
                "event_id": "intelx-run-4-all",
                "event_type": "intelx.news",
                "payload": {"headline": "Exchange notice", "source_agent": "intelx"},
            }
        ],
        order=order,
    )

    async def persist_notice(_event, _payload):
        order.append("memora_commit")

    monkeypatch.setattr(
        "futuris.integrations.memora_event_consumer._persist_notice_to_memora",
        persist_notice,
    )

    @asynccontextmanager
    async def sessions():
        yield FakeSession(rows, order=order)

    monkeypatch.setattr(
        "futuris.integrations.memora_event_consumer.async_session_factory", sessions
    )
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
    fake_client = FakeMemora(
        [
            {
                "id": 5,
                "event_id": "intelx-run-5-all",
                "event_type": "intelx.news",
                "payload": {"headline": "Exchange notice"},
            }
        ]
    )

    async def persist_notice(_event, _payload):
        memora_writes.append("persisted")

    monkeypatch.setattr(
        "futuris.integrations.memora_event_consumer._persist_notice_to_memora",
        persist_notice,
    )

    @asynccontextmanager
    async def sessions():
        yield FakeSession(rows, fail_commit=True)

    monkeypatch.setattr(
        "futuris.integrations.memora_event_consumer.async_session_factory", sessions
    )
    with pytest.raises(RuntimeError, match="database unavailable"):
        await consume_memora_events_once(fake_client)
    assert memora_writes == ["persisted"]
    assert fake_client.acks == []


@pytest.mark.asyncio
async def test_other_event_is_skipped_but_acknowledged_in_order(monkeypatch):
    fake_client = FakeMemora(
        [{"id": 6, "event_id": "evt-6", "event_type": "memory.created", "payload": {}}]
    )

    @asynccontextmanager
    async def sessions():
        yield FakeSession({})

    monkeypatch.setattr(
        "futuris.integrations.memora_event_consumer.async_session_factory", sessions
    )
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
        "sources": [
            {
                "url": "https://regulator.example/notices/123",
                "title": "Regulator notice",
                "publisher": "Example Regulator",
                "published_at": "2026-09-27T12:00:00Z",
                "trust_tier": "LIKELY_RELIABLE",
            },
            {"url": "file:///private/source"},
            {"url": "https://[malformed-host/source"},
            {"url": "https://user:secret@example.org/private"},
        ],
        "relevance": {
            "category": "regulatory_change",
            "confidence": 0.5,
            "domain": "intelx.news",
            "unexpected": "drop this",
        },
        "topics": ["stratex", "futuris"],
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
    assert body["provenance"]["evidence"]["sources"] == [
        {
            "url": "https://regulator.example/notices/123",
            "title": "Regulator notice",
            "publisher": "Example Regulator",
            "published_at": "2026-09-27T12:00:00Z",
            "trust_tier": "LIKELY_RELIABLE",
        }
    ]
    assert body["provenance"]["evidence"]["relevance"] == {
        "category": "regulatory_change",
        "confidence": 0.5,
        "domain": "intelx.news",
    }
    assert body["provenance"]["evidence"]["topics"] == ["stratex", "futuris"]


def test_webhook_symbol_selection_requires_explicit_supported_target():
    assert _symbol_from_explicit_targets([]) is None
    assert _symbol_from_explicit_targets(["market:crypto:ETHUSDT:volatility_24h"]) == "ETHUSDT"
    assert _symbol_from_explicit_targets(["BTC was mentioned in an article"]) is None
    assert _symbol_from_explicit_targets(["market:crypto:XRPUSDT"]) is None


@pytest.mark.asyncio
async def test_direct_webhook_is_durably_idempotent(monkeypatch):
    rows = {}
    memora_writes = []

    async def persist_memora_notice(_event, _payload):
        memora_writes.append((_event, _payload))

    monkeypatch.setattr(
        "futuris.api.routers.webhooks._persist_notice_to_memora",
        persist_memora_notice,
    )

    @asynccontextmanager
    async def sessions():
        yield FakeSession(rows)

    monkeypatch.setattr("futuris.api.routers.webhooks.async_session_factory", sessions)
    payload = IntelXResearchWebhookPayload(
        data=ResearchFindingEventData(
            run_id="run-unique-41",
            finding_summary="Exchange publishes a verified market notice.",
            recommended_forecast_targets=["market:crypto:ETHUSDT:volatility_24h"],
        )
    )
    key, inserted = await _store_webhook_notice(payload)
    retry_key, inserted_again = await _store_webhook_notice(payload)

    assert key == retry_key == "intelx-webhook:run-unique-41"
    assert inserted is True
    assert inserted_again is False
    assert len(rows) == 1
    assert rows[key].payload["finding_summary"] == payload.data.finding_summary
    assert len(memora_writes) == 2
    assert all(write[0]["event_id"] == key for write in memora_writes)
    assert all(
        write[1]["recommended_forecast_targets"] == payload.data.recommended_forecast_targets
        for write in memora_writes
    )


def test_durable_news_matching_requires_market_or_explicit_asset():
    from futuris.connectors.intelx_context import IntelXContextInjector

    matches = IntelXContextInjector._notice_matches_market
    assert matches({"headline": "generic headline"}, "BTCUSDT") is False
    assert matches({"headline": "market headline", "domain": "market"}, "BTCUSDT") is True
    assert matches({"asset": "ETH", "headline": "Ethereum notice"}, "ETHUSDT") is True
    assert matches({"asset": "SOL", "headline": "Solana notice"}, "ETHUSDT") is False


@pytest.mark.asyncio
async def test_durable_news_context_uses_only_matched_real_notices(monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from futuris.connectors.intelx_context import IntelXContextInjector

    notices = [
        SimpleNamespace(
            event_id="intelx-event-1",
            received_at=datetime(2026, 9, 29, 10, tzinfo=UTC),
            payload={
                "headline": "Bitcoin exchange filing",
                "asset": "BTC",
                "sentiment_score": -0.4,
                "volatility_impact_factor": 1.5,
            },
        ),
        SimpleNamespace(
            event_id="intelx-event-2",
            received_at=datetime(2026, 9, 29, 10, tzinfo=UTC),
            payload={"headline": "unclassified general article"},
        ),
    ]

    class Scalars:
        def all(self):
            return notices

    class Result:
        def scalars(self):
            return Scalars()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def execute(self, _query):
            return Result()

    @asynccontextmanager
    async def sessions():
        yield Session()

    monkeypatch.setattr("futuris.connectors.intelx_context.async_session_factory", sessions)
    reports = await IntelXContextInjector().fetch_durable_notice_context("BTCUSDT")

    assert len(reports) == 1
    assert reports[0].summary == "Bitcoin exchange filing"
    assert reports[0].sentiment_score == -0.4
    assert reports[0].volatility_impact_factor == 1.5


@pytest.mark.asyncio
async def test_intelx_query_baseline_and_missing_dates_are_not_evidence():
    from datetime import UTC, datetime

    from futuris.connectors.intelx_context import IntelXContextInjector

    now = datetime(2026, 9, 29, 12, tzinfo=UTC)
    payload = [
        {
            "report_id": "intelx-baseline-report",
            "asset_or_sector": "BTCUSDT",
            "published_at": now.isoformat(),
            "summary": "IntelX qualitative research baseline",
            "sentiment_score": 0.2,
            "volatility_impact_factor": 1.1,
            "tags": ["intelx", "baseline"],
        },
        {
            "report_id": "actual-but-undated",
            "asset_or_sector": "BTCUSDT",
            "summary": "This has no trustworthy publication time.",
            "sentiment_score": 0.9,
        },
    ]

    async def handler(_request: httpx.Request) -> httpx.Response:
        if _request.method == "POST":
            return httpx.Response(404)
        return httpx.Response(200, json=payload)

    injector = IntelXContextInjector(
        base_url="http://intelx.local",
        api_key="test-token",
        transport=httpx.MockTransport(handler),
    )
    assert await injector.fetch_recent_research("BTCUSDT", as_of=now) == []
