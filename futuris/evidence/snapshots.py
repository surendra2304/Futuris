"""Evidence snapshotting, immutable dataset freezing, and PII data minimization."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from futuris.core.enums import SignalClass
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


class EvidenceSnapshotter:
    """Freezes exact point-in-time input datasets as immutable Parquet snapshots."""

    def __init__(
        self,
        base_storage_path: str | None = None,
        denied_fields: set[str] | None = None,
    ) -> None:
        self.base_storage_path = Path(base_storage_path or settings.OBJECT_STORE_PATH)
        self.denied_fields = denied_fields or DEFAULT_DENIED_FIELDS

    def sanitize_dataframe(self, df) -> any:
        """Filter out denied sensitive/PII columns before snapshot freezing."""
        cols_to_drop = [c for c in df.columns if str(c).lower() in self.denied_fields]
        if cols_to_drop:
            return df.drop(columns=cols_to_drop)
        return df

    def freeze_snapshot(
        self,
        signal_set: TrustedSignalSet,
        as_of: datetime,
        forecast_id: UUID | None = None,
        source_id: str = "telemetry:synthetic",
        signal_class: SignalClass = SignalClass.TELEMETRY,
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
        with open(target_path, "rb") as f:
            file_bytes = f.read()
            content_hash = hashlib.sha256(file_bytes).hexdigest()

        trust_level = source_trust_registry.get_trust(source_id)

        return EvidenceRef(
            evidence_id=uuid4(),
            source=source_id,
            source_trust=trust_level,
            signal_class=signal_class,
            as_of=as_of,
            snapshot_path=str(target_path.resolve()),
            content_hash=content_hash,
        )
