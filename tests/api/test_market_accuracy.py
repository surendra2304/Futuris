from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from futuris.api.routers.market import get_market_accuracy
from futuris.storage.repositories import OutcomeRepository


async def _call_accuracy(monkeypatch: pytest.MonkeyPatch, pairs: list[tuple[object, object]]):
    async def fake_list(
        self: OutcomeRepository, *, target_prefix: str
    ) -> list[tuple[object, object]]:
        assert target_prefix == "market:"
        return pairs

    monkeypatch.setattr(OutcomeRepository, "list_resolved_with_forecasts", fake_list)
    return await get_market_accuracy(user=None, session=cast(AsyncSession, object()))


@pytest.mark.asyncio
async def test_no_resolved_outcomes_returns_null_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    response = await _call_accuracy(monkeypatch, [])

    assert response.status == "insufficient_data"
    assert response.total_evaluated == 0
    assert response.accuracy_pct is None
    assert response.brier_score is None
    assert response.sample_note == "no resolved market forecasts yet"


@pytest.mark.asyncio
async def test_missing_probabilities_do_not_report_perfect_brier_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forecast = SimpleNamespace(
        target="market:crypto:BTCUSDT:volatility_24h",
        range_lower=0.0,
        range_upper=10.0,
        probability=None,
    )
    outcome = SimpleNamespace(
        observed_value=5.0,
        event_occurred=False,
        resolved_at=datetime.now(UTC),
    )

    response = await _call_accuracy(monkeypatch, [(forecast, outcome)])

    assert response.status == "partial"
    assert response.total_evaluated == 1
    assert response.accuracy_pct == 100.0
    assert response.brier_score is None
    assert response.sample_note == "0 of 1 resolved forecasts carried a probability"


@pytest.mark.asyncio
async def test_brier_score_is_computed_only_from_observed_probabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forecast = SimpleNamespace(
        target="market:crypto:BTCUSDT:volatility_24h",
        range_lower=0.0,
        range_upper=10.0,
        probability=0.8,
    )
    outcome = SimpleNamespace(
        observed_value=5.0,
        event_occurred=True,
        resolved_at=datetime.now(UTC),
    )

    response = await _call_accuracy(monkeypatch, [(forecast, outcome)])

    assert response.status == "ACTIVE"
    assert response.accuracy_pct == 100.0
    assert response.brier_score == 0.04
