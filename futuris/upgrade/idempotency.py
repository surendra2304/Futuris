from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IdempotencyRecord:
    key: str
    request_hash: str
    status_code: int
    response: Any


class IdempotencyConflict(RuntimeError):
    pass


class IdempotencyStore:
    def __init__(self) -> None:
        self._records: dict[str, IdempotencyRecord] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def fingerprint(payload: Any) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(encoded).hexdigest()

    async def begin(self, key: str, payload: Any) -> IdempotencyRecord | None:
        if not key.strip():
            raise ValueError("idempotency key required")
        digest = self.fingerprint(payload)
        async with self._lock:
            previous = self._records.get(key)
            if previous and previous.request_hash != digest:
                raise IdempotencyConflict("same idempotency key used for different payload")
            return previous

    async def commit(self, key: str, payload: Any, status_code: int, response: Any) -> IdempotencyRecord:
        digest = self.fingerprint(payload)
        record = IdempotencyRecord(key, digest, status_code, response)
        async with self._lock:
            self._records[key] = record
        return record
