"""Regression tests for universe refresh pacing and matrix assembly.

These drive the real route handlers. An earlier version of this file tested a
helper directly and reimplemented the request shape inline, which meant it still
passed when both fixes were reverted -- so these call
``refresh_universe_predictions`` and ``build_universe_matrix`` themselves.

Two defects found by exercising the entry points rather than reading them:

1. ``POST /v1/predictions/refresh-all`` ran two independently budgeted passes --
   the refresh, then the matrix backfill -- so one request could spend the budget
   twice. With a degraded peer that is a 30s request behind a 15s budget.
2. ``as_of`` is tz-aware for forecasts written during the request and naive for
   rows read back from SQLite, so sorting a mix of the two raised TypeError.
   Reachable whenever a refresh stops partway: untouched targets keep their older
   naive rows while refreshed ones are still tz-aware in the identity map.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from futuris.api.routers.predictions import (
    build_universe_matrix,
    refresh_universe_predictions,
)
from futuris.core.enums import ConfidenceLevel, ForecastStatus
from futuris.core.schemas import Forecast
from futuris.core.universe_domains import UNIVERSE_TARGETS
from futuris.infra.auth import AuthUser


def _forecast(target: str, as_of: datetime) -> Forecast:
    return Forecast(
        forecast_id=uuid4(),
        target=target,
        as_of=as_of,
        horizon=timedelta(hours=24),
        expires_at=as_of + timedelta(hours=24),
        review_at=as_of + timedelta(hours=6),
        prediction=1.0,
        range_lower=0.0,
        range_upper=2.0,
        probability=0.5,
        confidence=ConfidenceLevel.MEDIUM,
        model_version="test",
        drivers=[],
        evidence=[],
        assumptions=[],
        status=ForecastStatus.ACTIVE,
    )


@pytest.mark.asyncio
async def test_one_request_spends_one_budget(monkeypatch):
    """refresh-all must hand its matrix pass what is left, not a fresh budget.

    Drives the real route handler and records the budget it passes down. The
    refresh is made to consume most of the budget without ever reaching it, so
    the remainder is clearly less than the default: reverting the shared deadline
    makes the matrix pass see the full default again and this fails.
    """
    import futuris.api.routers.predictions as predictions
    import futuris.core.universe_forecasting as uf

    per_target = 0.1
    assert per_target * len(UNIVERSE_TARGETS) < uf.REFRESH_BUDGET_SECONDS

    monkeypatch.setattr(predictions, "ForecastRepository", _EmptyRepo)

    async def slow_generate(
        target, context=None, session=None, skip_intelx=False, model_budget_seconds=None
    ):
        await asyncio.sleep(per_target)
        return _forecast(target, datetime.now(UTC))

    monkeypatch.setattr(uf, "generate_universe_forecast", slow_generate)

    real_matrix = predictions.build_universe_matrix
    seen: list[float] = []

    async def spy(session, **kwargs):
        seen.append(kwargs.get("budget_seconds"))
        return await real_matrix(session, **kwargs)

    monkeypatch.setattr(predictions, "build_universe_matrix", spy)

    response = await refresh_universe_predictions(
        background_tasks=None,
        user=AuthUser(label="test", role="analyst"),
        session=_AuditableSession(),
    )

    assert response.total_active_targets == len(UNIVERSE_TARGETS)
    assert seen, "the matrix assembly was never called"
    passed_down = seen[0]
    assert passed_down is not None, (
        "the matrix pass was given no budget, so it silently restarts the clock"
    )
    assert passed_down < uf.REFRESH_BUDGET_SECONDS, (
        f"the matrix pass received {passed_down}s, the full budget; the refresh had "
        f"already consumed {uf.REFRESH_BUDGET_SECONDS - passed_down:.2f}s of it"
    )
    assert passed_down <= uf.REFRESH_BUDGET_SECONDS


class _AuditableSession:
    """Accepts the writes the refresh route makes after the matrix pass (audit rows)."""

    def add(self, obj) -> None:
        return None

    async def flush(self) -> None:
        return None


class _EmptyRepo:
    """Stands in for ForecastRepository: a store with no active forecasts."""

    def __init__(self, session):
        self.session = session

    async def list_by_status(self, status):
        return []

    async def list_by_statuses(self, statuses):
        return []

    async def create(self, forecast):
        return forecast


@pytest.mark.asyncio
async def test_matrix_assembles_a_mix_of_aware_and_naive_forecasts(monkeypatch):
    """Rows written this request are tz-aware; rows read back are naive.

    Drives the real matrix assembly so reverting the sort key raises TypeError
    here rather than silently passing against a helper that is no longer used.
    """
    import futuris.api.routers.predictions as predictions
    import futuris.core.universe_forecasting as uf

    now = datetime.now(UTC)
    targets = list(UNIVERSE_TARGETS)
    mixed = [
        # odd indexes stay tz-aware (freshly written, still in the identity map),
        # even indexes come back naive (older rows this session did not write).
        _forecast(t, now if i % 2 else datetime.now(UTC).replace(tzinfo=None))
        for i, t in enumerate(targets)
    ]

    class _MixedRepo:
        def __init__(self, session):
            pass

        async def list_by_status(self, status):
            return list(mixed)

        async def list_by_statuses(self, statuses):
            return list(mixed)

    monkeypatch.setattr(predictions, "ForecastRepository", _MixedRepo)

    # Nothing is missing, so no generation and no outbound calls.
    async def _never(target, context=None, session=None, skip_intelx=False):  # pragma: no cover
        raise AssertionError("a warm store must not generate anything")

    monkeypatch.setattr(uf, "generate_universe_forecast", _never)

    response = await build_universe_matrix(session=object())

    assert response.total_active_targets == len(UNIVERSE_TARGETS)
    assert response.total_domains == 9
    # The newest forecast per target wins, whichever shape it arrived in.
    by_target = {item.target: item for d in response.domains for item in d.targets}
    assert len(by_target) == len(UNIVERSE_TARGETS)


@pytest.mark.asyncio
async def test_budget_below_one_unit_of_work_does_no_work(monkeypatch):
    import futuris.core.universe_forecasting as uf

    async def _never(target, context=None, session=None, skip_intelx=False):  # pragma: no cover
        raise AssertionError("nothing should be generated with no budget")

    monkeypatch.setattr(uf, "generate_universe_forecast", _never)

    assert await refresh_all_within_budget(object(), budget_seconds=0.0) == 0


@pytest.mark.asyncio
async def test_warm_store_makes_no_outbound_calls(monkeypatch):
    import futuris.core.universe_forecasting as uf

    async def _never(target, context=None, session=None, skip_intelx=False):  # pragma: no cover
        raise AssertionError("a fully-populated store must not call out")

    monkeypatch.setattr(uf, "generate_universe_forecast", _never)

    present = {t: object() for t in UNIVERSE_TARGETS}
    assert await refresh_missing_within_budget(object(), present, budget_seconds=30.0) == 0


from futuris.core.universe_forecasting import (  # noqa: E402
    refresh_all_within_budget,
    refresh_missing_within_budget,
)
