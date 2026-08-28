"""Tests for normalization, quality reports, and point-in-time contextualization."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from futuris.connectors.base import Observation
from futuris.features.contextualize import ContextLayer
from futuris.features.normalize import Normalizer


def test_normalizer_deduplication_and_gap_handling():
    """Verify last-write-wins deduplication, gap filling, and unit mismatch rejection."""
    t0 = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)
    observations = [
        Observation(
            observed_at=t0,
            source="src",
            series_id="test:rpm",
            value=100.0,
            unit="rpm",
        ),
        # Duplicate timestamp with updated value (last-write-wins)
        Observation(
            observed_at=t0,
            source="src",
            series_id="test:rpm",
            value=105.0,
            unit="rpm",
        ),
        # 5m step
        Observation(
            observed_at=t0 + timedelta(minutes=5),
            source="src",
            series_id="test:rpm",
            value=110.0,
            unit="rpm",
        ),
        # Missing 10m observation (under 15m gap), resumes at 15m
        Observation(
            observed_at=t0 + timedelta(minutes=15),
            source="src",
            series_id="test:rpm",
            value=120.0,
            unit="rpm",
        ),
    ]

    normalizer = Normalizer(grid_step_minutes=5, max_fill_gap_minutes=15)
    signal_set = normalizer.normalize(observations)

    assert signal_set.quality_report.duplicates_dropped == 1
    assert signal_set.quality_report.gaps_filled_under_15m == 1
    assert len(signal_set.values) == 4
    assert signal_set.values[0] == 105.0  # last write wins
    assert signal_set.values[2] == 110.0  # forward-filled gap at 10m


def test_normalizer_unit_mismatch_rejection():
    """Verify normalization rejects observations with unexpected units."""
    t0 = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)
    observations = [
        Observation(
            observed_at=t0,
            source="src",
            series_id="test:rpm",
            value=100.0,
            unit="rpm",
        ),
        Observation(
            observed_at=t0 + timedelta(minutes=5),
            source="src",
            series_id="test:rpm",
            value=110.0,
            unit="ms",  # invalid unit mismatch
        ),
    ]
    normalizer = Normalizer()
    with pytest.raises(ValueError, match="Unit mismatch"):
        normalizer.normalize(observations, expected_unit="rpm")


def test_context_layer_strict_point_in_time_contract():
    """Verify that feature calculation at time T strictly never leaks data from T+1."""
    t0 = datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)
    timestamps = [t0 + timedelta(minutes=5 * i) for i in range(100)]
    # Values: steady 100 for first 50 steps, then sudden massive spike to 5000 at step 51
    values = [100.0] * 50 + [5000.0] * 50

    from futuris.features.normalize import DataQualityReport, TrustedSignalSet

    report = DataQualityReport(
        total_raw_points=100,
        cleaned_points=100,
        duplicates_dropped=0,
        gaps_filled_under_15m=0,
        long_gaps_count=0,
        anomalies_clipped=0,
        coverage_percentage=100.0,
    )
    signal_set = TrustedSignalSet(
        series_id="checkout:rpm",
        unit="rpm",
        grid_step_minutes=5,
        start_time=timestamps[0],
        end_time=timestamps[-1],
        timestamps=timestamps,
        values=values,
        quality_report=report,
    )

    context = ContextLayer(rolling_windows=[6, 12])

    # 1. Feature table computed up to step 49 (before the spike)
    as_of_pre_spike = timestamps[49]
    df_pre_spike = context.build_feature_table(signal_set, as_of=as_of_pre_spike)

    # Rolling mean at step 49 must be exactly 100.0
    assert df_pre_spike.index[-1] == as_of_pre_spike
    assert np.isclose(df_pre_spike["rolling_mean_6"].iloc[-1], 100.0)
    assert np.isclose(df_pre_spike["rolling_max_6"].iloc[-1], 100.0)
    assert df_pre_spike["regime_high_load"].iloc[-1] == 0

    # 2. Feature table computed full range
    df_full = context.build_feature_table(signal_set)
    # The row at step 49 in df_full MUST IDENTICALLY match df_pre_spike
    row_at_49_in_full = df_full.loc[as_of_pre_spike]
    assert np.isclose(row_at_49_in_full["rolling_mean_6"], 100.0)
    assert np.isclose(row_at_49_in_full["rolling_max_6"], 100.0)
    assert row_at_49_in_full["regime_high_load"] == 0
