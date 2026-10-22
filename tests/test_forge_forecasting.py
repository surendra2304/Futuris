"""Tests for ForgeConnector and ForgeBuildPredictor."""

from datetime import UTC, datetime

import httpx
import pytest

from futuris.connectors.forge import (
    TARGET_BUILD_SUCCESS_PROBABILITY,
    TARGET_CAPACITY_EXHAUSTION,
    ForgeConnector,
)
from futuris.models.forge_predictor import (
    ForgeBuildPredictor,
    TaskComplexityProfile,
)


@pytest.mark.asyncio
async def test_forge_connector_parsing():
    """Verify ForgeConnector ingests and normalizes build telemetry."""
    start = datetime(2026, 8, 28, 10, 0, 0, tzinfo=UTC)
    end = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)

    mock_telemetry = [
        {
            "timestamp": "2026-08-28T10:00:00Z",
            "metric_type": "submission_rate",
            "value": 4.5,
        },
        {
            "timestamp": "2026-08-28T10:05:00Z",
            "metric_type": "build_duration",
            "value": 185.0,
            "template_id": "fastapi_react",
        },
        {
            "timestamp": "2026-08-28T10:10:00Z",
            "metric_type": "verification_pass_rate",
            "value": 0.94,
        },
        {
            "timestamp": "2026-08-28T10:15:00Z",
            "metric_type": "disk_usage",
            "value": 14.2,
        },
    ]

    async def _handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" in request.headers
        return httpx.Response(200, json=mock_telemetry)

    transport = httpx.MockTransport(_handler)
    connector = ForgeConnector(
        base_url="http://forge-cluster.local",
        api_key="forge_test_key",
        transport=transport,
    )

    observations = await connector.fetch(start, end)
    assert len(observations) == 4
    assert observations[0].series_id == "forge:task:submission_rate"
    assert observations[1].series_id == "forge:build:duration"
    assert observations[2].series_id == "forge:verification:pass_rate"
    assert observations[3].series_id == "forge:workspace:disk_usage"
    assert TARGET_BUILD_SUCCESS_PROBABILITY == "forge:build:success_probability_first_attempt"
    assert TARGET_CAPACITY_EXHAUSTION == "forge:capacity:exhaustion_24h"


def test_forge_build_predictor_characteristics():
    """Verify ForgeBuildPredictor computes durations and queuing actions."""
    predictor = ForgeBuildPredictor()

    profile = TaskComplexityProfile(
        file_count=12,
        lines_of_code=850,
        dependency_count=6,
        has_custom_mcp=True,
        has_frontend_build=True,
    )

    history = {
        "vanilla_python": [True, False, True],
        "fastapi_react_fullstack_v2": [True, True, True, True],
    }

    # 1. Nominal Load -> Proceed immediately
    res_nominal = predictor.predict_build_characteristics(
        task=profile,
        template_history=history,
        concurrent_tasks=3,
        max_concurrent_limit=10,
    )
    assert res_nominal.predicted_duration_seconds > 60.0
    assert res_nominal.first_attempt_success_probability > 0.50
    assert res_nominal.recommended_template == "fastapi_react_fullstack_v2"
    assert res_nominal.recommended_action == "PROCEED_IMMEDIATELY"

    # 2. High Load -> Queue task
    res_high_load = predictor.predict_build_characteristics(
        task=profile,
        template_history=history,
        concurrent_tasks=9,
        max_concurrent_limit=10,
    )
    assert res_high_load.capacity_exhaustion_risk > 0.75
    assert res_high_load.recommended_action == "QUEUE_TASK"
