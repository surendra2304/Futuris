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
    assert "Historical accuracy for this target type: 88%" in narrative


def test_prometheus_metrics_scrape_endpoint():
    """Verify /metrics returns Prometheus formatted metric payloads."""
    resp = metrics_endpoint()
    assert resp.status_code == 200
    assert b"forecasts_created_total" in resp.body
    assert b"calibration_error_gauge" in resp.body
