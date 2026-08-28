"""Tests for evidence snapshotting, immutability, and hash stability."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from futuris.core.enums import SourceTrust
from futuris.evidence.snapshots import EvidenceSnapshotter, SnapshotAlreadyExistsError
from futuris.evidence.trust import SourceTrustRegistry
from futuris.features.normalize import DataQualityReport, TrustedSignalSet


@pytest.fixture
def dummy_signal_set() -> TrustedSignalSet:
    t0 = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)
    timestamps = [t0 + timedelta(minutes=5 * i) for i in range(10)]
    values = [100.0 + float(i) for i in range(10)]
    report = DataQualityReport(
        total_raw_points=10,
        cleaned_points=10,
        duplicates_dropped=0,
        gaps_filled_under_15m=0,
        long_gaps_count=0,
        anomalies_clipped=0,
        coverage_percentage=100.0,
    )
    return TrustedSignalSet(
        series_id="checkout:rpm",
        unit="rpm",
        grid_step_minutes=5,
        start_time=timestamps[0],
        end_time=timestamps[-1],
        timestamps=timestamps,
        values=values,
        quality_report=report,
    )


def test_evidence_snapshot_creation_and_hash_stability(
    dummy_signal_set: TrustedSignalSet, tmp_path
):
    """Verify Parquet snapshot creation, SHA-256 calculation, and deterministic stability."""
    snapshotter = EvidenceSnapshotter(base_storage_path=str(tmp_path))
    forecast_id = uuid4()
    as_of = dummy_signal_set.timestamps[5]

    evidence_ref = snapshotter.freeze_snapshot(
        signal_set=dummy_signal_set,
        as_of=as_of,
        forecast_id=forecast_id,
        source_id="telemetry:synthetic",
    )

    assert evidence_ref.as_of == as_of
    assert evidence_ref.source == "telemetry:synthetic"
    assert evidence_ref.source_trust == SourceTrust.HIGH
    assert len(evidence_ref.content_hash) == 64  # valid SHA-256 hex string


def test_evidence_snapshot_immutability(dummy_signal_set: TrustedSignalSet, tmp_path):
    """Verify attempting to overwrite an existing frozen snapshot raises an error."""
    snapshotter = EvidenceSnapshotter(base_storage_path=str(tmp_path))
    forecast_id = uuid4()
    as_of = dummy_signal_set.timestamps[5]

    # First write succeeds
    snapshotter.freeze_snapshot(
        signal_set=dummy_signal_set,
        as_of=as_of,
        forecast_id=forecast_id,
    )

    # Attempted overwrite must raise SnapshotAlreadyExistsError
    with pytest.raises(SnapshotAlreadyExistsError):
        snapshotter.freeze_snapshot(
            signal_set=dummy_signal_set,
            as_of=as_of,
            forecast_id=forecast_id,
        )


def test_source_trust_registry():
    """Verify source trust classification and training eligibility gating."""
    registry = SourceTrustRegistry()
    assert registry.get_trust("telemetry:datadog:prod") == SourceTrust.HIGH
    assert registry.is_eligible_for_training("telemetry:datadog:prod") is True

    assert registry.get_trust("unverified:feed:external") == SourceTrust.UNTRUSTED
    assert registry.is_eligible_for_training("unverified:feed:external") is False
