from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from futuris.api.routers.friday import FridayForecastRequest, _friday_forecast_response
from futuris.core.enums import ConfidenceLevel, ForecastStatus


def _forecast(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "forecast_id": uuid4(),
        "probability": None,
        "predictive_distribution": None,
        "range_lower": 0.0,
        "prediction": 0.0,
        "range_upper": 0.0,
        "drivers": [],
        "calibration_metrics": {"n_resolved_outcomes": 0, "ece": 0.0},
        "status": ForecastStatus.INSUFFICIENT_DATA,
        "confidence": ConfidenceLevel.INSUFFICIENT_DATA,
        "model_version": "none",
        "model_metadata": {"reason": "no scored input"},
        "intervals": None,
        "assumptions": [],
        "evidence": [],
        "evidence_class": None,
        "evidence_source": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _request() -> FridayForecastRequest:
    return FridayForecastRequest(
        friday_request_id="req_truthful_response",
        target="service:checkout:capacity_exceedance_24h",
        horizon="24h",
    )


def test_insufficient_forecast_does_not_publish_zero_estimates_or_scores() -> None:
    response = _friday_forecast_response(_request(), _forecast())

    assert response.status == ForecastStatus.INSUFFICIENT_DATA.value
    assert response.prediction.point_estimate is None
    assert response.prediction.lower_bound is None
    assert response.prediction.upper_bound is None
    assert response.prediction.probability_distribution is None
    assert response.predictive_distribution is None
    assert response.intervals is None
    assert response.calibration_score is None
    assert response.calibration_metrics["calibration_status"] == "uncalibrated"
    assert response.assumptions == []


def test_zero_probability_is_preserved_without_neutral_default() -> None:
    forecast = _forecast(
        status=ForecastStatus.ACTIVE,
        confidence=ConfidenceLevel.HIGH,
        prediction=12.0,
        range_lower=10.0,
        range_upper=14.0,
        probability=0.0,
        calibration_metrics={"n_resolved_outcomes": 0, "ece": 0.0},
    )
    response = _friday_forecast_response(_request(), forecast)

    assert response.prediction.point_estimate == 12.0
    assert response.prediction.probability_distribution is not None
    assert response.prediction.probability_distribution["exceedance_probability"] == 0.0
    assert response.calibration_score is None


def test_missing_probability_does_not_reuse_distribution_probability() -> None:
    forecast = _forecast(
        status=ForecastStatus.ACTIVE,
        confidence=ConfidenceLevel.HIGH,
        prediction=12.0,
        range_lower=10.0,
        range_upper=14.0,
        probability=None,
        predictive_distribution={
            "exceedance_probability": 0.5,
            "p10": 10.0,
            "p50": 12.0,
            "p90": 14.0,
        },
    )
    response = _friday_forecast_response(_request(), forecast)

    assert response.prediction.probability_distribution is not None
    assert "exceedance_probability" not in response.prediction.probability_distribution
    assert response.evidence_class is None


def test_calibration_requires_finite_measured_ece_and_positive_sample_count() -> None:
    from futuris.api.routers.friday import _honest_calibration

    forecast = _forecast(
        status=ForecastStatus.ACTIVE,
        confidence=ConfidenceLevel.HIGH,
        calibration_metrics={"n_resolved_outcomes": 10, "ece": float("nan")},
    )
    score, metrics = _honest_calibration(forecast)
    assert score is None
    assert metrics["calibration_status"] == "uncalibrated"
