"""Tests for DriverAnalyzer correlation, DecisionSupport, and architectural boundaries."""

import inspect
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import numpy as np
import pandas as pd

from futuris.core.decision import DecisionSupport
from futuris.core.enums import ConfidenceLevel, ForecastStatus, SignalClass, SourceTrust
from futuris.core.schemas import Driver, EvidenceRef, Forecast
from futuris.features.drivers import DriverAnalyzer


def test_driver_analyzer_detects_leading_indicator_and_lag_bucket():
    """Verify that a synthetic signal leading target by 30 mins is classified leading."""
    analyzer = DriverAnalyzer(max_lag_steps=12, step_minutes=5)
    steps = 500

    base_wave = np.sin(2 * np.pi * np.arange(steps) / 100.0)

    # Injected Leading feature: shifts forward by 6 steps (30 mins at 5m resolution)
    lead_steps = 6
    lead_feature = np.roll(base_wave, -lead_steps)
    target = base_wave

    classification, optimal_lag_mins, corr = analyzer.compute_lead_lag(
        pd.Series(lead_feature), pd.Series(target)
    )

    assert classification == "leading"
    assert optimal_lag_mins == 30
    assert corr > 0.80


def test_driver_analyzer_detects_correlation_degradation():
    """Verify that a driver that turns to pure noise in the second half is flagged as degraded."""
    analyzer = DriverAnalyzer()
    rng = np.random.default_rng(42)

    # First half: perfectly correlated signal
    first_half = np.linspace(0, 100, 300)
    target_first = first_half + rng.normal(0, 2, size=300)

    # Second half: noise uncorrelated signal
    second_half = rng.normal(50, 20, size=300)
    target_second = np.linspace(100, 200, 300)

    feat_vals = np.concatenate([first_half, second_half])
    target_vals = np.concatenate([target_first, target_second])

    long_corr, rec_corr, is_degraded = analyzer.evaluate_driver_degradation(
        pd.Series(feat_vals), pd.Series(target_vals), recent_window_steps=288
    )

    assert is_degraded is True
    assert rec_corr < 0.50 * long_corr


def test_decision_support_urgency_and_approval_gating():
    """Verify DecisionSupport assigns correct urgency buckets and requires approval."""
    ds = DecisionSupport()
    as_of = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)
    evidence_id = uuid4()

    forecast_urgent = Forecast(
        forecast_id=uuid4(),
        target="service:checkout:capacity_exceedance_24h",
        as_of=as_of,
        horizon=timedelta(hours=4),
        expires_at=as_of + timedelta(hours=4),
        prediction=4200.0,
        range_lower=3800.0,
        range_upper=4600.0,
        probability=0.85,
        confidence=ConfidenceLevel.HIGH,
        drivers=[
            Driver(
                name="traffic_spike",
                direction="positive",
                strength=0.9,
                leading_or_lagging="leading",
                evidence_refs=[evidence_id],
            )
        ],
        evidence=[
            EvidenceRef(
                evidence_id=evidence_id,
                source="telemetry:synthetic",
                source_trust=SourceTrust.HIGH,
                signal_class=SignalClass.TELEMETRY,
                as_of=as_of,
                snapshot_path="/tmp/snap.parquet",
                content_hash="mockhash",
            )
        ],
        model_version="auto_arima@v1:hash",
        assumptions=["traffic regime stable"],
        review_at=as_of + timedelta(hours=1),
        status=ForecastStatus.ACTIVE,
    )

    imp = ds.implications(forecast_urgent, capacity_threshold=4000.0)
    assert imp.urgency == "now"
    assert "High risk" in imp.expected_impact
    assert "traffic_spike" in imp.watch_list

    recs = ds.recommendations(forecast_urgent)
    assert len(recs) >= 2
    for r in recs:
        if r.action_type in ["scale_capacity", "enable_traffic_shedding_policy"]:
            assert r.requires_approval is True


def test_decision_support_architectural_safety_boundary():
    """Architectural boundary enforcement: assert decision module has zero execution imports."""
    import futuris.core.decision as decision_module

    source_code = inspect.getsource(decision_module)

    forbidden = [
        "futuris.connectors",
        "httpx",
        "requests",
        "urllib.request",
        "subprocess",
        "socket",
    ]

    for term in forbidden:
        msg = f"Safety violation: {term} imported in decision module!"
        assert f"import {term}" not in source_code, msg
        assert f"from {term}" not in source_code, msg
