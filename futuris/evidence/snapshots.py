"""Evidence snapshotting and immutable point-in-time dataset freezing."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from futuris.core.enums import SignalClass
from futuris.core.schemas import EvidenceRef
from futuris.evidence.trust import source_trust_registry
from futuris.features.normalize import TrustedSignalSet
from futuris.infra.config import settings


class SnapshotAlreadyExistsError(Exception):
    """Raised when an attempt is made to overwrite an immutable frozen snapshot."""


class EvidenceSnapshotter:
    """Freezes exact point-in-time input datasets as immutable Parquet snapshots."""

    def __init__(self, base_storage_path: str | None = None) -> None:
        self.base_storage_path = Path(base_storage_path or settings.OBJECT_STORE_PATH)

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
        df_sliced.to_parquet(str(target_path), index=True, engine="pyarrow")

        # Compute SHA-256 hash
        with open(target_path, "rb") as f:
            file_bytes = f.read()
            content_hash = hashlib.sha256(file_bytes).hexdigest()

        source_trust = source_trust_registry.get_trust(source_id)

        return EvidenceRef(
            evidence_id=uuid4(),
            source=source_id,
            source_trust=source_trust,
            signal_class=signal_class,
            as_of=as_of,
            snapshot_path=str(target_path),
            content_hash=content_hash,
        )
