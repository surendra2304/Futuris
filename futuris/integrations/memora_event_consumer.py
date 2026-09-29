"""Consume Memora events into a durable, advisory Futuris inbox."""
from __future__ import annotations

import asyncio
import hashlib
import math
from typing import Any
from urllib.parse import urlsplit

import httpx

from futuris.infra.config import settings
from futuris.infra.logging import get_logger
from futuris.integrations.memora_client import memora_client
from futuris.storage.db import async_session_factory
from futuris.storage.models import IntelXNoticeModel

logger = get_logger("futuris.memora_events")
CONSUMER_ID = "futuris-cloud-v1"
POLL_INTERVAL_SECONDS = 15


def _result_ok(result: Any) -> bool:
    return isinstance(result, dict) and result.get("status") not in {"error", "failed"}


async def consume_memora_events_once(client=memora_client) -> int:
    """Persist/skip events in feed order, acknowledging only after durable handling."""
    cursor = await asyncio.to_thread(client.read_event_cursor, "futuris", CONSUMER_ID)
    if not _result_ok(cursor) or not isinstance(cursor.get("after_id"), int):
        raise RuntimeError("Memora event cursor is unavailable")

    feed = await asyncio.to_thread(client.poll_events, "futuris", cursor["after_id"], 100)
    if not _result_ok(feed) or not isinstance(feed.get("events"), list):
        raise RuntimeError("Memora event feed is unavailable")

    handled = 0
    for event in feed["events"]:
        if not isinstance(event, dict) or not isinstance(event.get("id"), int):
            raise RuntimeError("Memora returned a malformed event; cursor was not advanced")
        event_id = event["id"]
        event_type = event.get("event_type")
        if event_type == "intelx.news":
            payload = event.get("payload")
            if not isinstance(payload, dict):
                raise RuntimeError("IntelX event payload is malformed; cursor was not advanced")
            async with async_session_factory() as session:
                stable_event_id = str(event.get("event_id", event_id))
                existing = await session.get(IntelXNoticeModel, stable_event_id)
                if existing is None:
                    await _persist_notice_to_memora(event, payload)
                    session.add(
                        IntelXNoticeModel(
                            event_id=stable_event_id,
                            source_agent=str(payload.get("source_agent", "intelx"))[:64],
                            payload=payload,
                        )
                    )
                    await session.commit()
        # Other event types are deliberately ignored by this consumer, but must
        # still be acknowledged in order to avoid blocking later IntelX notices.
        ack = await asyncio.to_thread(
            client.acknowledge_event, "futuris", event_id, CONSUMER_ID
        )
        if not _result_ok(ack):
            raise RuntimeError(f"Memora did not acknowledge event {event_id}")
        handled += 1
    return handled


async def _persist_notice_to_memora(event: dict[str, Any], payload: dict[str, Any]) -> None:
    """Write the advisory to Memora with a stable idempotency key before acking."""
    if not settings.MEMORA_API_KEY:
        raise RuntimeError("Futuris Memora credential is not configured")
    headline = str(payload.get("headline", "")).strip()[:2000]
    summary = str(payload.get("summary", "")).strip()[:8000]
    if not headline and not summary:
        raise RuntimeError("IntelX event has no headline or summary")
    stable_event_id = str(event.get("event_id", event.get("id", "")))
    idempotency_key = "futuris-intelx-" + hashlib.sha256(stable_event_id.encode()).hexdigest()[:48]
    evidence: dict[str, Any] = {}
    if isinstance(payload.get("source_url"), str):
        evidence["source_url"] = payload["source_url"][:2048]
    if isinstance(payload.get("sources"), list):
        sources = []
        for item in payload["sources"][:20]:
            # IntelX sends structured public-source references. Preserve the
            # provenance fields while accepting legacy string URLs too.
            if isinstance(item, str):
                source = {"url": item.strip()[:2048]}
            elif isinstance(item, dict) and isinstance(item.get("url"), str):
                source = {"url": item["url"].strip()[:2048]}
                for field, limit in (
                    ("title", 300),
                    ("domain", 255),
                    ("publisher", 255),
                    ("published_at", 64),
                    ("trust_tier", 32),
                ):
                    value = item.get(field)
                    if isinstance(value, str) and value.strip():
                        source[field] = value.strip()[:limit]
            else:
                continue
            try:
                parsed = urlsplit(source["url"])
                hostname = parsed.hostname
            except ValueError:
                continue
            if (
                parsed.scheme.lower() in {"http", "https"}
                and hostname
                and not parsed.username
                and not parsed.password
            ):
                sources.append(source)
        if sources:
            evidence["sources"] = sources
    if isinstance(payload.get("topics"), list):
        evidence["topics"] = [
            item[:100] for item in payload["topics"][:50] if isinstance(item, str)
        ]
    for field in ("symbol", "asset", "asset_or_sector"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            evidence[field] = value.strip()[:100]
    for field in ("symbols", "assets", "target_assets", "recommended_forecast_targets"):
        values = payload.get(field)
        if isinstance(values, list):
            evidence[field] = [
                item.strip()[:100]
                for item in values[:50]
                if isinstance(item, str) and item.strip()
            ]
    for field, lower, upper in (
        ("sentiment_score", -1.0, 1.0),
        ("volatility_impact_factor", 0.5, 3.0),
    ):
        value = payload.get(field)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and lower <= value <= upper
        ):
            evidence[field] = float(value)
    if isinstance(payload.get("relevance"), (int, float)):
        evidence["relevance"] = payload["relevance"]
    elif isinstance(payload.get("relevance"), dict):
        relevance = payload["relevance"]
        normalized_relevance: dict[str, Any] = {}
        for field in ("category", "domain"):
            value = relevance.get(field)
            if isinstance(value, str) and value.strip():
                normalized_relevance[field] = value.strip()[:255]
        confidence = relevance.get("confidence")
        if (
            isinstance(confidence, (int, float))
            and not isinstance(confidence, bool)
            and math.isfinite(confidence)
        ):
            normalized_relevance["confidence"] = max(0.0, min(1.0, float(confidence)))
        if normalized_relevance:
            evidence["relevance"] = normalized_relevance
    if isinstance(payload.get("published_at"), str):
        evidence["published_at"] = payload["published_at"][:64]
    content = "\n".join(part for part in (headline, summary) if part)
    body = {
        "agent_id": "futuris",
        "idempotency_key": idempotency_key,
        "content_text": content,
        "target_namespace_path": "memora://futuris/intelx/notices",
        "memory_type": "episodic",
        "source": "agent:intelx",
        "source_type": "web_scrape",
        "trust_level": "untrusted",
        "confidence": 0.0,
        "importance": 0.5,
        "provenance": {
            "event_id": stable_event_id,
            "event_type": event.get("event_type"),
            "source_agent": payload.get("source_agent", "intelx"),
            "evidence": evidence,
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.MEMORA_API_KEY}",
        "X-Agent-Name": "futuris",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=10.0) as http:
        response = await http.post(
            f"{settings.MEMORA_URL.rstrip('/')}/v1/memories",
            json=body,
            headers=headers,
        )
    if response.status_code not in {200, 201}:
        raise RuntimeError(f"Memora rejected IntelX notice with HTTP {response.status_code}")
    receipt = response.json()
    if not isinstance(receipt, dict) or not receipt.get("id"):
        raise RuntimeError("Memora did not return a durable memory receipt")


async def memora_event_worker() -> None:
    """Long-running cloud worker; transient errors leave the cursor unchanged."""
    if not settings.MEMORA_API_KEY:
        logger.warning("memora_event_consumer_disabled_missing_futuris_credential")
        return
    while True:
        try:
            count = await consume_memora_events_once()
            if count:
                logger.info("memora_events_consumed", count=count, consumer_id=CONSUMER_ID)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("memora_event_consumer_retry", error=type(exc).__name__)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
