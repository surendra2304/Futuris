from __future__ import annotations

from types import SimpleNamespace

import pytest

from futuris.api.routers.evaluation import get_calibration
from futuris.core.enums import EvidenceClass


@pytest.mark.asyncio
async def test_calibration_with_no_paired_samples_reports_no_score() -> None:
    async def no_pairs(*, target_prefix: str, limit: int):
        assert target_prefix == "service:checkout:capacity_exceedance_24h"
        assert limit == 5000
        return []

    repo = SimpleNamespace(list_resolved_with_forecasts=no_pairs)
    response = await get_calibration(
        user=None,
        target="service:checkout:capacity_exceedance_24h",
        outcome_repo=repo,
    )

    assert response.data_freshness == "insufficient_data"
    assert response.sample_count == 0
    assert response.expected_calibration_error is None
    assert response.bin_centers == []
    assert response.observed_frequencies == []
    assert response.evidence_class is None


@pytest.mark.asyncio
async def test_calibration_with_paired_samples_reports_measured_score() -> None:
    forecasts = [
        SimpleNamespace(probability=0.8),
        SimpleNamespace(probability=0.2),
    ]
    outcomes = [
        SimpleNamespace(event_occurred=True),
        SimpleNamespace(event_occurred=False),
    ]
    pairs = list(zip(forecasts, outcomes, strict=True))

    async def resolved_pairs(*, target_prefix: str, limit: int):
        assert target_prefix == "service:checkout:capacity_exceedance_24h"
        assert limit == 5000
        return pairs

    repo = SimpleNamespace(list_resolved_with_forecasts=resolved_pairs)
    response = await get_calibration(
        user=None,
        target="service:checkout:capacity_exceedance_24h",
        outcome_repo=repo,
    )

    assert response.data_freshness == "live_persisted"
    assert response.sample_count == 2
    assert response.expected_calibration_error is not None
    assert response.expected_calibration_error >= 0.0
    assert response.evidence_class == EvidenceClass.DERIVED
