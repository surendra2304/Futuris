"""Regression checks preventing fabricated Stratex observations."""

from datetime import UTC, datetime

import httpx
import pytest

from futuris.connectors.trading_bot import TradingBotConnector


@pytest.mark.asyncio
async def test_unavailable_stratex_does_not_create_fallback_observations():
    start = datetime(2026, 8, 28, 10, 0, 0, tzinfo=UTC)
    end = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "upstream unavailable"})

    connector = TradingBotConnector(
        base_url="http://stratex.local",
        api_key="test-token",
        transport=httpx.MockTransport(handler),
    )
    assert await connector.fetch(start, end) == []
