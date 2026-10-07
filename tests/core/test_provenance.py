"""Regression tests for served-value provenance (bug C3).

The project's promise is that a client can always tell whether a served number
was measured, derived, generated or seeded. These tests pin that promise for
each fabrication site found by the audit.
"""

from datetime import UTC, datetime, timedelta

import pytest

from futuris.core.enums import EvidenceClass
from futuris.core.hashing import EMPTY_SHA256, content_hash_of, is_real_hash
from futuris.core.schemas import EvidenceRef, Forecast


def _forecast(**overrides) -> Forecast:
    now = datetime.now(UTC)
    base = {
        "target": "t",
        "as_of": now,
        "horizon": timedelta(hours=24),
        "expires_at": now + timedelta(hours=24),
        "review_at": now + timedelta(hours=6),
        "prediction": 1.0,
        "range_lower": 0.5,
        "range_upper": 1.5,
        "confidence": "medium",
        "model_version": "test@v1",
    }
    return Forecast(**{**base, **overrides})


# ── hashing helpers ────────────────────────────────────────────────────────


def test_content_hash_is_deterministic_and_order_independent():
    assert content_hash_of({"a": 1, "b": 2}) == content_hash_of({"b": 2, "a": 1})
    assert content_hash_of({"a": 1}) != content_hash_of({"a": 2})
    assert len(content_hash_of({"a": 1})) == 64


def test_empty_string_digest_is_not_a_real_hash():
    assert is_real_hash(EMPTY_SHA256) is False
    assert is_real_hash("") is False
    assert is_real_hash("not-hex" * 8) is False
    assert is_real_hash(content_hash_of({"a": 1})) is True


# ── schema invariants ──────────────────────────────────────────────────────


def test_live_evidence_cannot_use_the_empty_hash():
    with pytest.raises(ValueError, match="real SHA-256 content hash"):
        EvidenceRef(
            source="s",
            source_trust="high",
            signal_class="telemetry",
            as_of=datetime.now(UTC),
            snapshot_path="p",
            content_hash=EMPTY_SHA256,
            evidence_class=EvidenceClass.LIVE,
        )


def test_unlabelled_forecast_defaults_to_synthetic():
    """A forecast that does not declare its provenance is never called measured."""
    assert _forecast().evidence_class == EvidenceClass.SYNTHETIC


# ── pipeline outputs ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_reports_no_measured_calibration_without_outcomes():
    """A pipeline run must not claim ECE or calibration it did not measure."""
    from futuris.core.pipeline import ForecastingPipeline

    result = await ForecastingPipeline().run(
        target="service:checkout:capacity_exceedance_24h",
        as_of=datetime.now(UTC),
        horizon=timedelta(hours=24),
        lookback_days=5,
    )
    metrics = result.forecast.calibration_metrics

    assert "ece" not in metrics
    assert metrics.get("n_resolved_outcomes") == 0
    assert metrics.get("calibration_status") == "uncalibrated"
    # The implied Brier score is computable from the probability alone and is real.
    assert "brier_score" in metrics


@pytest.mark.asyncio
async def test_pipeline_forecast_is_labelled_synthetic_when_telemetry_is_synthetic():
    from futuris.core.pipeline import ForecastingPipeline

    result = await ForecastingPipeline().run(
        target="service:checkout:capacity_exceedance_24h",
        as_of=datetime.now(UTC),
        horizon=timedelta(hours=24),
        lookback_days=5,
    )
    assert result.forecast.evidence_class == EvidenceClass.SYNTHETIC
    assert result.forecast.evidence_source == "telemetry:synthetic"


@pytest.mark.asyncio
async def test_pipeline_confidence_does_not_assume_resolved_history():
    """Sparse resolution history must lower confidence, not be invented away."""
    from futuris.core.pipeline import ForecastingPipeline

    result = await ForecastingPipeline().run(
        target="service:checkout:capacity_exceedance_24h",
        as_of=datetime.now(UTC),
        horizon=timedelta(hours=24),
        lookback_days=5,
    )
    assert result.forecast.confidence.value == "low"


# ── universe forecasts ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_universe_context_forecast_echoes_the_caller_and_labels_it():
    from futuris.core.universe_forecasting import generate_universe_forecast

    forecast = await generate_universe_forecast(
        "memora:storage:capacity_exhaustion_days",
        context={"point_estimate": 45.0},
        skip_intelx=True,
    )

    assert forecast.prediction == 45.0
    assert forecast.evidence_class == EvidenceClass.SYNTHETIC
    assert forecast.evidence_source == "caller_supplied_context"
    assert "caller_supplied_context" in forecast.assumptions
    # The band around a single supplied estimate is a stated policy, not a measurement.
    assert any("range_derived" in a for a in forecast.assumptions)
    assert forecast.model_metadata["prediction_is_not_authorization"] is True


@pytest.mark.asyncio
async def test_universe_context_forecast_hashes_the_context_it_used():
    from futuris.core.universe_forecasting import generate_universe_forecast

    forecast = await generate_universe_forecast(
        "memora:storage:capacity_exhaustion_days",
        context={"point_estimate": 45.0, "note": "operator estimate"},
        skip_intelx=True,
    )
    ref = forecast.evidence[0]

    assert ref.evidence_class == EvidenceClass.SYNTHETIC
    assert ref.content_hash != EMPTY_SHA256
    assert is_real_hash(ref.content_hash)


@pytest.mark.asyncio
async def test_universe_forecast_without_context_is_labelled_and_computed():
    """No caller estimate: the number comes from the pipeline, labelled synthetic."""
    from futuris.core.universe_forecasting import generate_universe_forecast

    forecast = await generate_universe_forecast(
        "friday:orchestration:system_health_24h", skip_intelx=True
    )

    assert forecast.evidence_class in (EvidenceClass.SYNTHETIC, EvidenceClass.LIVE)
    assert forecast.evidence_source is not None
    if forecast.status.value == "insufficient_data":
        assert forecast.model_metadata["blocked_reason"] == "insufficient_data_detected"
        assert forecast.prediction == 0.0 and forecast.range_lower == forecast.range_upper == 0.0
        assert forecast.evidence == []
    else:
        # A served number must never be one of the old hardcoded placeholders.
        assert forecast.prediction not in {94.5, 2840.0, 48.0}
        assert forecast.evidence, "a served forecast must carry its evidence"


@pytest.mark.asyncio
async def test_universe_insufficient_data_forecast_carries_no_invented_values():
    """When the pipeline cannot produce a number the forecast says so plainly."""
    import futuris.core.universe_forecasting as uf

    class _FailingPipeline:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run(self, *_args, **_kwargs):
            raise RuntimeError("telemetry source unavailable")

    import futuris.core.pipeline as pipeline_module

    original = pipeline_module.ForecastingPipeline
    pipeline_module.ForecastingPipeline = _FailingPipeline
    try:
        forecast = await uf.generate_universe_forecast(
            "friday:orchestration:system_health_24h", skip_intelx=True
        )
    finally:
        pipeline_module.ForecastingPipeline = original

    assert forecast.status.value == "insufficient_data"
    assert forecast.confidence.value == "insufficient_data"
    assert forecast.evidence == []
    assert forecast.drivers == []
    assert forecast.evidence_source is None
    assert "telemetry source unavailable" in forecast.model_metadata["error"]
    assert forecast.calibration_metrics["error"] == "insufficient_data"


# ── calibration analyzer ───────────────────────────────────────────────────


def test_calibration_analyzer_reports_no_result_without_samples():
    from futuris.evaluation.calibration import update_calibration_metrics

    metrics = update_calibration_metrics([], [])
    assert metrics["calibration_status"] == "uncalibrated"
    assert metrics["n_resolved_outcomes"] == 0
    assert "is_calibrated" not in metrics


def test_calibration_analyzer_marks_measured_results():
    from futuris.evaluation.calibration import update_calibration_metrics

    metrics = update_calibration_metrics([0.1, 0.9], [False, True])
    assert metrics["calibration_status"] == "measured"
    assert metrics["n_resolved_outcomes"] == 2
    assert 0.0 <= metrics["ece"] <= 1.0
