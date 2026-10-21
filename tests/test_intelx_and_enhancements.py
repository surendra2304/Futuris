"""Tests for IntelX Context Injector, AI-Universe enhancer, narrative generator, and metrics."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from futuris.connectors.intelx_context import IntelXContextInjector
from futuris.core.enums import ConfidenceLevel, ForecastStatus
from futuris.core.schemas import Driver, Forecast
from futuris.infra.metrics import metrics_endpoint
from futuris.models.ai_universe_enhanced import (
    AIUniverseModelEnhancer,
    ForecastExplanationGenerator,
)


@pytest.mark.asyncio
async def test_intelx_context_injection_and_adjustments():
    """Verify IntelX research reports are fetched and parsed into exogenous modifiers."""
    start = datetime(2026, 8, 28, 10, 0, 0, tzinfo=UTC)

    mock_reports = [
        {
            "report_id": str(uuid4()),
            "asset_or_sector": "fintech_checkout",
            "published_at": "2026-08-28T09:00:00Z",
            "summary": "High transaction surge anticipated during seasonal promo",
            "sentiment_score": 0.40,
            "volatility_impact_factor": 1.30,
            "key_findings": ["Volume growth +35%"],
            "tags": ["retail", "ecommerce"],
        }
    ]

    async def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_reports)

    transport = httpx.MockTransport(_handler)
    injector = IntelXContextInjector(
        base_url="http://intelx.local",
        api_key="intelx_key",
        transport=transport,
    )

    reports = await injector.fetch_recent_research("fintech_checkout", as_of=start)
    assert len(reports) == 1
    assert reports[0].sentiment_score == 0.40

    adj = injector.compute_exogenous_adjustments(reports)
    assert adj["sentiment_multiplier"] == 1.04
    assert adj["volatility_multiplier"] == 1.30


@pytest.mark.asyncio
async def test_intelx_unavailable_returns_no_invented_research():
    """An unavailable IntelX service must not become fabricated sentiment evidence."""

    async def _unavailable(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    injector = IntelXContextInjector(
        base_url="http://intelx.local",
        api_key="intelx_key",
        transport=httpx.MockTransport(_unavailable),
    )

    reports = await injector.fetch_recent_research("BTCUSDT")

    assert reports == []
    assert injector.compute_exogenous_adjustments(reports) == {
        "sentiment_multiplier": 1.0,
        "volatility_multiplier": 1.0,
        "confidence_penalty": 0.0,
    }


def test_ai_universe_model_enhancement_and_narrative():
    """Verify AI-Universe model enhancer adjusts confidence intervals and formats narrative."""
    enhancer = AIUniverseModelEnhancer()

    # 1. Enhance Forecast Uncertainty
    enhancement = enhancer.enhance_forecast(
        base_prediction=3500.0,
        range_lower=3200.0,
        range_upper=3800.0,
        context_factors={"volatility_multiplier": 1.40, "sentiment_multiplier": 1.0},
    )
    assert enhancement.adjusted_lower_bound < 3200.0
    assert enhancement.adjusted_upper_bound > 3800.0
    assert enhancement.confidence_interval_expansion_pct > 0.0
    assert len(enhancement.risk_factors) > 0

    # 2. Generate Human-Readable Narrative
    now = datetime.now(UTC)
    forecast = Forecast(
        forecast_id=uuid4(),
        target="service:checkout:capacity_exceedance_24h",
        as_of=now,
        horizon=timedelta(hours=24),
        expires_at=now + timedelta(hours=24),
        prediction=3850.0,
        range_lower=3400.0,
        range_upper=4300.0,
        probability=0.74,
        confidence=ConfidenceLevel.HIGH,
        drivers=[
            Driver(
                name="traffic_volume",
                direction="positive",
                strength=0.84,
                leading_or_lagging="leading",
            )
        ],
        evidence=[],
        assumptions=["Baseline scaling"],
        review_at=now + timedelta(hours=6),
        status=ForecastStatus.ACTIVE,
        model_version="auto_arima@v1",
    )

    narrative = ForecastExplanationGenerator.generate_narrative_explanation(
        forecast, historical_accuracy_pct=88.0
    )
    assert "service:checkout:capacity_exceedance_24h" in narrative
    assert "3850.00" in narrative
    assert "Primary driver:" in narrative
    assert "Historical accuracy: 88.0% (caller-supplied measured value)" in narrative



@pytest.mark.parametrize(
    "invalid",
    [float("nan"), float("inf"), -0.1, 100.1, True],
)
def test_narrative_rejects_invalid_historical_accuracy(invalid: float) -> None:
    forecast = _sample_forecast_for_narrative()
    with pytest.raises(ValueError, match="finite percentage"):
        ForecastExplanationGenerator.generate_narrative_explanation(
            forecast, historical_accuracy_pct=invalid
        )


def _sample_forecast_for_narrative(**overrides: object) -> Forecast:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "forecast_id": uuid4(),
        "target": "service:checkout:capacity_exceedance_24h",
        "as_of": now,
        "horizon": timedelta(hours=24),
        "expires_at": now + timedelta(hours=24),
        "prediction": 3850.0,
        "range_lower": 3400.0,
        "range_upper": 4300.0,
        "probability": None,
        "confidence": ConfidenceLevel.HIGH,
        "drivers": [],
        "evidence": [],
        "assumptions": [],
        "review_at": now + timedelta(hours=6),
        "status": ForecastStatus.ACTIVE,
        "model_version": "auto_arima@v1",
    }
    values.update(overrides)
    return Forecast(**values)


def test_default_narrative_does_not_invent_metrics_or_explanations() -> None:
    narrative = ForecastExplanationGenerator.generate_narrative_explanation(
        _sample_forecast_for_narrative(probability=0.0)
    )
    assert "event probability 0.0%" in narrative
    assert "not identified from the available forecast evidence" in narrative
    assert "Historical accuracy: not measured for this target" in narrative
    assert "Calibration metrics: not measured from resolved outcomes" in narrative
    assert "87%" not in narrative
    assert "15%" not in narrative
    assert "0.82" not in narrative
    assert "positive momentum" not in narrative
    assert "correlation" not in narrative
    assert "not authorization to act" in narrative



def test_ai_universe_enhancer_applies_configured_confidence_scaling() -> None:
    enhancer = AIUniverseModelEnhancer(confidence_scaling=1.25)
    result = enhancer.enhance_forecast(
        base_prediction=50.0,
        range_lower=40.0,
        range_upper=60.0,
    )
    assert result.adjusted_lower_bound == 37.5
    assert result.adjusted_upper_bound == 62.5
    assert result.confidence_interval_expansion_pct == 25.0
    assert "No additional exogenous risk modifier was reported" in result.risk_factors


@pytest.mark.parametrize(
    "kwargs",
    [
        {"confidence_scaling": 0.9},
        {"confidence_scaling": float("nan")},
        {"confidence_scaling": float("inf")},
        {"confidence_scaling": True},
    ],
)
def test_ai_universe_rejects_invalid_confidence_scaling(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="confidence_scaling"):
        AIUniverseModelEnhancer(**kwargs)


@pytest.mark.parametrize(
    "factors",
    [
        {"volatility_multiplier": 0.0},
        {"volatility_multiplier": float("nan")},
        {"sentiment_multiplier": float("inf")},
        {"sentiment_multiplier": -1.0},
        {"volatility_multiplier": "not-numeric"},
    ],
)
def test_ai_universe_rejects_invalid_context_multipliers(factors: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="multiplier"):
        AIUniverseModelEnhancer().enhance_forecast(10.0, 8.0, 12.0, factors)


def test_ai_universe_does_not_infer_regulatory_risks_from_sentiment() -> None:
    result = AIUniverseModelEnhancer().enhance_forecast(
        base_prediction=10.0,
        range_lower=8.0,
        range_upper=12.0,
        context_factors={"sentiment_multiplier": 0.9},
    )
    assert any("specific cause not inferred" in risk for risk in result.risk_factors)
    assert all("regulatory developments" not in risk for risk in result.risk_factors)



def test_ai_universe_preserves_asymmetric_interval_when_scaling() -> None:
    result = AIUniverseModelEnhancer(confidence_scaling=1.2).enhance_forecast(
        base_prediction=100.0,
        range_lower=70.0,
        range_upper=110.0,
    )
    assert result.adjusted_lower_bound == 64.0
    assert result.adjusted_upper_bound == 112.0


def test_zero_width_interval_reports_zero_measured_expansion() -> None:
    result = AIUniverseModelEnhancer().enhance_forecast(
        base_prediction=10.0,
        range_lower=10.0,
        range_upper=10.0,
    )
    assert result.adjusted_lower_bound == 10.0
    assert result.adjusted_upper_bound == 10.0
    assert result.confidence_interval_expansion_pct == 0.0


def test_prometheus_metrics_scrape_endpoint():
    """Verify /metrics returns Prometheus formatted metric payloads."""
    resp = metrics_endpoint()
    assert resp.status_code == 200
    assert b"forecasts_created_total" in resp.body
    assert b"calibration_error_gauge" in resp.body
