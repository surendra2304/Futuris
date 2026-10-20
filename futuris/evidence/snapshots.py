"""Evidence snapshotting, immutable dataset freezing, and PII data minimization."""

import contextlib
import hashlib
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pandas as pd

from futuris.core.enums import EvidenceClass, SignalClass
from futuris.core.schemas import EvidenceRef
from futuris.evidence.trust import source_trust_registry
from futuris.features.normalize import TrustedSignalSet
from futuris.infra.config import settings

# Default PII and sensitive data field denylist
DEFAULT_DENIED_FIELDS = {
    "user_id",
    "email",
    "ip_address",
    "ssn",
    "credit_card",
    "phone",
    "customer_name",
    "user_pii",
}


class SnapshotAlreadyExistsError(Exception):
    """Raised when an attempt is made to overwrite an immutable frozen snapshot."""


class SnapshotTamperedError(Exception):
    """Raised when SHA-256 hash validation fails, indicating unauthorized data tampering."""


class SnapshotNotFoundError(Exception):
    """Raised when an evidence snapshot file cannot be located on disk."""


class EvidenceSnapshotter:
    """Freezes exact point-in-time input datasets as immutable Parquet snapshots."""

    def __init__(
        self,
        base_storage_path: str | None = None,
        denied_fields: set[str] | None = None,
    ) -> None:
        self.base_storage_path = Path(base_storage_path or settings.OBJECT_STORE_PATH)
        self.denied_fields = denied_fields or DEFAULT_DENIED_FIELDS

    def sanitize_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Filter out denied sensitive/PII columns before snapshot freezing."""
        cols_to_drop = [c for c in df.columns if str(c).lower() in self.denied_fields]
        if cols_to_drop:
            return df.drop(columns=cols_to_drop)
        return df

    def _mark_file_read_only(self, path: Path) -> None:
        """Set read-only permissions on frozen parquet snapshot."""
        with contextlib.suppress(Exception):
            os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)

    def freeze_snapshot(
        self,
        signal_set: TrustedSignalSet,
        as_of: datetime,
        forecast_id: UUID | None = None,
        source_id: str = "telemetry:synthetic",
        signal_class: SignalClass = SignalClass.TELEMETRY,
        evidence_class: EvidenceClass = EvidenceClass.SYNTHETIC,
    ) -> EvidenceRef:
        """Freeze exact slice of TrustedSignalSet on or before as_of to immutable Parquet."""
        as_of = as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of.astimezone(UTC)

        df = signal_set.to_dataframe()
        df_sliced = df[df.index <= as_of].copy()
        if df_sliced.empty:
            msg = "Cannot freeze empty snapshot: no data before as_of."
            raise ValueError(msg)

        # Apply data minimization filtering
        df_clean = self.sanitize_dataframe(df_sliced)

        forecast_tag = str(forecast_id or uuid4())
        dir_path = self.base_storage_path / forecast_tag
        dir_path.mkdir(parents=True, exist_ok=True)

        as_of_str = as_of.strftime("%Y%m%dT%H%M%SZ")
        file_name = f"snapshot_{signal_set.series_id.replace(':', '_')}_{as_of_str}.parquet"
        target_path = dir_path / file_name

        if target_path.exists():
            msg = f"Snapshot file '{target_path}' already exists and is immutable."
            raise SnapshotAlreadyExistsError(msg)

        # Write Parquet with pyarrow
        df_clean.to_parquet(str(target_path), index=True, engine="pyarrow")

        # Compute SHA-256 hash
        file_bytes = target_path.read_bytes()
        content_hash = hashlib.sha256(file_bytes).hexdigest()

        # Enforce read-only immutability
        self._mark_file_read_only(target_path)

        trust_level = source_trust_registry.get_trust(source_id)

        return EvidenceRef(
            evidence_id=uuid4(),
            source=source_id,
            source_trust=trust_level,
            signal_class=signal_class,
            as_of=as_of,
            snapshot_path=str(target_path.resolve()),
            content_hash=content_hash,
            evidence_class=evidence_class,
        )

    def freeze_dataframe(
        self,
        df: pd.DataFrame,
        series_id: str,
        as_of: datetime,
        forecast_id: UUID | None = None,
        source_id: str = "telemetry:live",
        signal_class: SignalClass = SignalClass.TELEMETRY,
        evidence_class: EvidenceClass = EvidenceClass.LIVE,
    ) -> EvidenceRef:
        """Freeze exact DataFrame slice on or before as_of to immutable Parquet."""
        as_of = as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of.astimezone(UTC)

        df_copy = df.copy()
        if isinstance(df_copy.index, pd.DatetimeIndex):
            df_sliced = df_copy[df_copy.index <= as_of]
        elif "observed_at" in df_copy.columns:
            df_copy["observed_at"] = pd.to_datetime(df_copy["observed_at"], utc=True)
            df_sliced = df_copy[df_copy["observed_at"] <= as_of]
        elif "timestamp" in df_copy.columns:
            df_copy["timestamp"] = pd.to_datetime(df_copy["timestamp"], utc=True)
            df_sliced = df_copy[df_copy["timestamp"] <= as_of]
        else:
            df_sliced = df_copy

        if df_sliced.empty:
            msg = "Cannot freeze empty snapshot: no data before as_of."
            raise ValueError(msg)

        df_clean = self.sanitize_dataframe(df_sliced)

        forecast_tag = str(forecast_id or uuid4())
        dir_path = self.base_storage_path / forecast_tag
        dir_path.mkdir(parents=True, exist_ok=True)

        as_of_str = as_of.strftime("%Y%m%dT%H%M%SZ")
        file_name = f"snapshot_{series_id.replace(':', '_')}_{as_of_str}.parquet"
        target_path = dir_path / file_name

        if target_path.exists():
            msg = f"Snapshot file '{target_path}' already exists and is immutable."
            raise SnapshotAlreadyExistsError(msg)

        df_clean.to_parquet(str(target_path), index=True, engine="pyarrow")

        file_bytes = target_path.read_bytes()
        content_hash = hashlib.sha256(file_bytes).hexdigest()

        self._mark_file_read_only(target_path)

        trust_level = source_trust_registry.get_trust(source_id)

        return EvidenceRef(
            evidence_id=uuid4(),
            source=source_id,
            source_trust=trust_level,
            signal_class=signal_class,
            as_of=as_of,
            snapshot_path=str(target_path.resolve()),
            content_hash=content_hash,
            evidence_class=evidence_class,
        )

    def verify_snapshot(self, evidence_ref: EvidenceRef) -> bool:
        """Verify on-disk file exists and SHA-256 matches evidence_ref.content_hash."""
        file_path = Path(evidence_ref.snapshot_path)
        if not file_path.exists():
            msg = f"Snapshot file not found: {evidence_ref.snapshot_path}"
            raise SnapshotNotFoundError(msg)

        file_bytes = file_path.read_bytes()
        computed_hash = hashlib.sha256(file_bytes).hexdigest()

        if computed_hash != evidence_ref.content_hash:
            msg = (
                f"Snapshot integrity failure for evidence {evidence_ref.evidence_id}: "
                f"expected {evidence_ref.content_hash}, got {computed_hash}"
            )
            raise SnapshotTamperedError(msg)

        return True

    def load_snapshot(self, evidence_ref: EvidenceRef) -> pd.DataFrame:
        """Load frozen parquet data after verifying cryptographic hash integrity."""
        self.verify_snapshot(evidence_ref)
        return pd.read_parquet(evidence_ref.snapshot_path, engine="pyarrow")
