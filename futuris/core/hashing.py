"""Content hashing for inline (non-Parquet) evidence references.

Snapshots frozen by :class:`~futuris.evidence.snapshots.EvidenceSnapshotter`
hash the Parquet file they write. Evidence that is carried inline — a caller
context, a peer probe response, a generated series — still needs a hash that a
consumer can recompute, so it is hashed over its canonical JSON form.

``EMPTY_SHA256`` is the digest of zero bytes. It was previously served as the
content hash for evidence that did not exist; the helpers below make that
impossible to do silently.
"""

import hashlib
import json
from datetime import date, datetime
from typing import Any
from uuid import UUID

# sha256(b"") -- never a valid hash for served evidence.
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _default(value: Any) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def canonical_json(obj: Any) -> str:
    """Serialize ``obj`` deterministically so its hash is reproducible."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_default)


def content_hash_of(obj: Any) -> str:
    """SHA-256 over the canonical JSON form of ``obj``."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def is_real_hash(value: str | None) -> bool:
    """True when ``value`` looks like a genuine SHA-256 digest of some content."""
    if not value or len(value) != 64:
        return False
    if value == EMPTY_SHA256:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True
