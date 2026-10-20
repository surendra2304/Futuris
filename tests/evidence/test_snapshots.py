"""Tests for evidence snapshotting, immutability, and hash stability."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
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


def test_freeze_dataframe_and_verify_tamper_detection(tmp_path):
    """Test freeze_dataframe, verify_snapshot, tamper detection, and loading."""
    import pandas as pd
    from futuris.core.enums import EvidenceClass
    from futuris.evidence.snapshots import SnapshotNotFoundError, SnapshotTamperedError

    snapshotter = EvidenceSnapshotter(base_storage_path=str(tmp_path))
    now = datetime.now(UTC)

    df = pd.DataFrame(
        {
            "observed_at": [now - timedelta(minutes=10), now - timedelta(minutes=5), now],
            "value": [10.5, 20.0, 30.5],
            "user_id": ["u1", "u2", "u3"],  # PII field to be sanitized
            "email": ["a@b.com", "c@d.com", "e@f.com"],  # PII field
        }
    )

    ev_ref = snapshotter.freeze_dataframe(
        df=df,
        series_id="service:payment:throughput",
        as_of=now,
        source_id="telemetry:datadog:prod",
        evidence_class=EvidenceClass.LIVE,
    )

    assert ev_ref.evidence_class == EvidenceClass.LIVE
    assert len(ev_ref.content_hash) == 64

    # Verify snapshot integrity
    assert snapshotter.verify_snapshot(ev_ref) is True

    # Load snapshot and ensure PII was dropped
    loaded_df = snapshotter.load_snapshot(ev_ref)
    assert len(loaded_df) == 3
    assert "user_id" not in loaded_df.columns
    assert "email" not in loaded_df.columns
    assert "value" in loaded_df.columns

    # Test tampering detection: alter bytes on disk
    path = Path(ev_ref.snapshot_path)
    # Temporarily remove read-only to simulate malicious modification
    import stat
    path.chmod(stat.S_IWRITE | stat.S_IREAD)
    with open(path, "ab") as f:
        f.write(b"corrupted_tampered_bytes")

    with pytest.raises(SnapshotTamperedError):
        snapshotter.verify_snapshot(ev_ref)

    with pytest.raises(SnapshotTamperedError):
        snapshotter.load_snapshot(ev_ref)

    # Test missing file
    ev_ref_missing = ev_ref.model_copy(update={"snapshot_path": str(tmp_path / "nonexistent.parquet")})
    with pytest.raises(SnapshotNotFoundError):
        snapshotter.verify_snapshot(ev_ref_missing)
