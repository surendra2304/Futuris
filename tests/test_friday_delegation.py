"""Tests for FRIDAY delegation endpoints, scenarios, calibration reports, and rate limiting."""

from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from futuris.api.app import app
from futuris.api.deps import get_db_session
from futuris.infra.config import settings
from futuris.storage.models import Base


@pytest.fixture
async def friday_test_db():
    """Isolated in-memory SQLite database for FRIDAY delegation tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_friday_forecast_delegation_authorized(friday_test_db: AsyncSession, monkeypatch):
    """Verify POST /v1/friday/forecast receives delegation and returns response."""
    friday_key = "friday_test_key_1234567890abcdef012345"
    monkeypatch.setattr(settings, "FUTURIS_FRIDAY_API_KEY", friday_key)

    async def _override_get_db():
        yield friday_test_db

    app.dependency_overrides[get_db_session] = _override_get_db
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Test unauthorized without key
        unauth_resp = await client.post(
            "/v1/friday/forecast",
            json={
                "friday_request_id": "req_101",
                "target": "service:checkout:capacity_exceedance_24h",
                "horizon": "24h",
            },
        )
        assert unauth_resp.status_code == 401

        # 2. Test authorized request
        resp = await client.post(
            "/v1/friday/forecast",
            headers={"X-API-Key": friday_key},
            json={
                "friday_request_id": "req_101",
                "target": "service:checkout:capacity_exceedance_24h",
                "horizon": "24h",
                "confidence_level": 0.90,
                "context": {"requesting_system": "FRIDAY_AUTO_SCALER"},
                "priority": "urgent",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["friday_request_id"] == "req_101"
        assert "prediction" in data
        assert data["prediction"]["point_estimate"] > 0
        assert "probability_distribution" in data["prediction"]
        assert len(data["drivers_identified"]) > 0

        # 3. Test scenario evaluation POST /v1/friday/scenario
        forecast_id = data["futuris_forecast_id"]
        scen_resp = await client.post(
            "/v1/friday/scenario",
            headers={"X-API-Key": friday_key},
            json={
                "question": "What if traffic doubles?",
                "base_forecast_id": forecast_id,
                "scenario_spec": {
                    "variable_changes": [{"metric": "demand", "change_pct": 100.0}],
                    "assumptions": ["Cluster size constant"],
                },
            },
        )
        assert scen_resp.status_code == 200
        scen_data = scen_resp.json()
        assert scen_data["divergent_prediction"] > data["prediction"]["point_estimate"]
        assert scen_data["risk_assessment"] == "HIGH_RISK"
        assert "comparison_to_baseline" in scen_data

        # 4. List forecasts GET /v1/friday/forecasts
        list_resp = await client.get(
            "/v1/friday/forecasts",
            headers={"X-API-Key": friday_key},
        )
        assert list_resp.status_code == 200
        assert len(list_resp.json()) >= 1

        # 5. Get FRIDAY calibration report GET /v1/friday/calibration
        cal_resp = await client.get(
            "/v1/friday/calibration",
            headers={"X-API-Key": friday_key},
        )
        assert cal_resp.status_code == 200
        cal_data = cal_resp.json()
        assert "overall_ece" in cal_data
        assert cal_data["overall_ece"] is None
        assert cal_data["per_target_type_calibration"] == {}
        assert cal_data["trend"] == "insufficient_data"
        assert cal_data["recent_accuracy_summary"]["calibration_status"] == "uncalibrated"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_friday_calibration_report_uses_resolved_pairs_only() -> None:
    from futuris.api.routers.friday import get_friday_calibration

    pairs = []
    for probability in [0.9] * 5 + [0.1] * 5:
        pairs.append(
            (
                SimpleNamespace(
                    target="service:checkout:capacity_exceedance_24h",
                    probability=probability,
                    range_lower=0.0,
                    range_upper=2.0,
                ),
                SimpleNamespace(event_occurred=False, observed_value=1.0),
            )
        )
    pairs.append(
        (
            SimpleNamespace(
                target="service:uncalibrated",
                probability=None,
                range_lower=90.0,
                range_upper=110.0,
            ),
            SimpleNamespace(event_occurred=True, observed_value=100.0),
        )
    )

    class OutcomeRepoStub:
        async def list_resolved_with_forecasts(self, limit: int):
            assert limit == 5000
            return pairs

    report = await get_friday_calibration(outcome_repo=OutcomeRepoStub())

    assert report.overall_ece == pytest.approx(0.5)
    assert report.per_target_type_calibration == {
        "service:checkout:capacity_exceedance_24h": pytest.approx(0.5)
    }
    assert report.trend == "improving"
    assert report.recent_accuracy_summary == {
        "brier_score": pytest.approx(0.41),
        "range_coverage": 1.0,
        "interval_sample_count": 11,
        "nominal_interval_coverage": {},
        "resolved_samples": 10,
        "calibration_status": "measured",
    }


@pytest.mark.asyncio
async def test_friday_calibration_measures_only_declared_nominal_intervals() -> None:
    from futuris.api.routers.friday import get_friday_calibration

    pairs = [
        (
            SimpleNamespace(
                target="service:declared",
                probability=0.5,
                range_lower=0.0,
                range_upper=2.0,
                intervals=[{"nominal_coverage": 0.9, "lower": 0.0, "upper": 2.0}],
            ),
            SimpleNamespace(event_occurred=True, observed_value=1.0),
        ),
        (
            SimpleNamespace(
                target="service:declared",
                probability=0.5,
                range_lower=0.0,
                range_upper=2.0,
                intervals=[{"nominal_coverage": 0.9, "lower": 10.0, "upper": 20.0}],
            ),
            SimpleNamespace(event_occurred=False, observed_value=1.0),
        ),
        (
            SimpleNamespace(
                target="service:generic",
                probability=0.5,
                range_lower=0.0,
                range_upper=2.0,
                intervals=[{"lower": 0.0, "upper": 2.0}],
            ),
            SimpleNamespace(event_occurred=True, observed_value=1.0),
        ),
        (
            SimpleNamespace(
                target="service:invalid-level",
                probability=0.5,
                range_lower=0.0,
                range_upper=2.0,
                intervals=[{"nominal_coverage": 1.0, "lower": 0.0, "upper": 2.0}],
            ),
            SimpleNamespace(event_occurred=True, observed_value=1.0),
        ),
        (
            SimpleNamespace(
                target="service:ambiguous",
                probability=0.5,
                range_lower=0.0,
                range_upper=2.0,
                intervals=[
                    {"nominal_coverage": 0.8, "lower": 0.0, "upper": 2.0},
                    {"nominal_coverage": 0.8, "lower": 0.0, "upper": 2.0},
                ],
            ),
            SimpleNamespace(event_occurred=True, observed_value=1.0),
        ),
        (
            SimpleNamespace(
                target="service:declared",
                probability=0.5,
                range_lower=0.0,
                range_upper=2.0,
                intervals=[{"nominal_coverage": 0.8, "lower": 0.0, "upper": 0.0}],
            ),
            SimpleNamespace(event_occurred=False, observed_value=1.0),
        ),
    ]

    class OutcomeRepoStub:
        async def list_resolved_with_forecasts(self, limit: int):
            assert limit == 5000
            return pairs

    report = await get_friday_calibration(outcome_repo=OutcomeRepoStub())

    assert report.recent_accuracy_summary["range_coverage"] == 1.0
    assert report.recent_accuracy_summary["nominal_interval_coverage"] == {
        "0.8": {"empirical_coverage": 0.0, "sample_count": 1},
        "0.9": {"empirical_coverage": 0.5, "sample_count": 2},
    }


def test_friday_response_does_not_infer_nominal_coverage_from_range_bounds() -> None:
    from uuid import uuid4

    from futuris.api.routers.friday import (
        FridayForecastRequest,
        _friday_forecast_response,
    )

    forecast = SimpleNamespace(
        probability=None,
        predictive_distribution={},
        range_lower=10.0,
        range_upper=20.0,
        prediction=15.0,
        drivers=[],
        calibration_metrics={},
        forecast_id=uuid4(),
        confidence=SimpleNamespace(value="medium"),
        status=SimpleNamespace(value="ACTIVE"),
        intervals=[],
        model_metadata={},
        assumptions=[],
        evidence=[],
        model_version="test:v1",
    )
    request = FridayForecastRequest(target="service:test")

    response = _friday_forecast_response(request, forecast)

    assert response.intervals == [{"lower": 10.0, "upper": 20.0}]
    assert "nominal_coverage" not in response.intervals[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("earlier_probability", "recent_probability", "sample_count", "expected_trend"),
    [
        (0.20, 0.211, 10, "degrading"),
        (0.20, 0.21, 10, "stable"),
        (0.20, 0.30, 9, "insufficient_data"),
    ],
)
async def test_friday_calibration_trend_uses_chronological_sample_halves(
    earlier_probability: float,
    recent_probability: float,
    sample_count: int,
    expected_trend: str,
) -> None:
    from futuris.api.routers.friday import get_friday_calibration

    split_at = sample_count // 2
    probabilities = [earlier_probability] * split_at + [recent_probability] * (
        sample_count - split_at
    )
    # The repository returns resolved pairs oldest first; recent samples are
    # therefore in the second half.
    pairs = [
        (
            SimpleNamespace(
                target="service:checkout:capacity_exceedance_24h",
                probability=probability,
                range_lower=0.0,
                range_upper=2.0,
            ),
            SimpleNamespace(event_occurred=False, observed_value=1.0),
        )
        for probability in probabilities
    ]

    class OutcomeRepoStub:
        async def list_resolved_with_forecasts(self, limit: int):
            assert limit == 5000
            return pairs

    report = await get_friday_calibration(outcome_repo=OutcomeRepoStub())

    assert report.trend == expected_trend


@pytest.mark.asyncio
async def test_delegated_forecast_carries_the_research_context(
    friday_test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FRIDAY delegates a target and expects the ecosystem's research applied.

    The live mesh found this gap: a delegated forecast was computed in complete
    isolation (zero calls to the research peer), even though every other
    producer enriches with IntelX context.  The findings must show up as
    interpretability drivers plus explicit metadata, without moving the
    statistical estimate.
    """
    from datetime import UTC, datetime
    from uuid import uuid4

    from futuris.connectors.intelx_context import IntelXResearchReport

    friday_key = "friday_test_key_1234567890abcdef012345"
    monkeypatch.setattr(settings, "FUTURIS_FRIDAY_API_KEY", friday_key)

    reports = [
        IntelXResearchReport(
            report_id=uuid4(),
            asset_or_sector="service:checkout",
            published_at=datetime.now(UTC),
            summary="Checkout latency spiked after the payment gateway rollout",
            sentiment_score=-0.35,
            volatility_impact_factor=1.4,
            key_findings=["gateway rollout correlates with the latency spike"],
            tags=["checkout"],
        )
    ]

    async def fake_reports(target, as_of, timeout_seconds=1.5):  # noqa: ANN001, ANN202
        return reports

    monkeypatch.setattr("futuris.api.routers.friday.fetch_research_reports", fake_reports)

    async def _override_get_db():
        yield friday_test_db

    app.dependency_overrides[get_db_session] = _override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/friday/forecast",
            headers={"X-API-Key": friday_key},
            json={
                "friday_request_id": "req_research_context",
                "target": "service:checkout:capacity_exceedance_24h",
                "horizon": "24h",
            },
        )
    app.dependency_overrides.clear()

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["model_metadata"]["intelx_context_included"] is True
    assert body["model_metadata"]["research_reports_considered"] == 1
    metrics = [driver["metric"] for driver in body["drivers_identified"]]
    assert any(metric.startswith("intelx:") for metric in metrics), metrics
    # The estimate itself is still the pipeline's, and still advisory only.
    assert body["prediction_is_not_authorization"] is True
    assert body["executable_commands"] == []


@pytest.mark.asyncio
async def test_delegation_survives_an_absent_research_peer(
    friday_test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No research peer: the forecast still lands and says research was absent."""
    friday_key = "friday_test_key_1234567890abcdef012345"
    monkeypatch.setattr(settings, "FUTURIS_FRIDAY_API_KEY", friday_key)

    async def no_reports(target, as_of, timeout_seconds=1.5):  # noqa: ANN001, ANN202
        return []

    monkeypatch.setattr("futuris.api.routers.friday.fetch_research_reports", no_reports)

    async def _override_get_db():
        yield friday_test_db

    app.dependency_overrides[get_db_session] = _override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/friday/forecast",
            headers={"X-API-Key": friday_key},
            json={
                "friday_request_id": "req_no_research",
                "target": "service:checkout:capacity_exceedance_24h",
                "horizon": "24h",
            },
        )
    app.dependency_overrides.clear()

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["model_metadata"]["intelx_context_included"] is False
    assert body["model_metadata"]["research_reports_considered"] == 0
