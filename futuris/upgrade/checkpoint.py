from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class Checkpoint:
    job_id: str
    version: int
    state: str
    payload: dict[str, Any]
    checksum: str
    created_at: str


class CheckpointManager:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.conn = connection
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS checkpoints(
                job_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                state TEXT NOT NULL,
                payload TEXT NOT NULL,
                checksum TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(job_id,version)
            )"""
        )
        self.conn.commit()

    @staticmethod
    def _checksum(job_id: str, version: int, state: str, payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            {"job_id": job_id, "version": version, "state": state, "payload": payload},
            sort_keys=True, separators=(",", ":"),
        ).encode()
        return hashlib.sha256(canonical).hexdigest()

    def save(self, job_id: str, version: int, state: str, payload: dict[str, Any]) -> Checkpoint:
        checksum = self._checksum(job_id, version, state, payload)
        created = datetime.now(UTC).isoformat()
        self.conn.execute(
            "INSERT INTO checkpoints VALUES(?,?,?,?,?,?)",
            (job_id, version, state, json.dumps(payload), checksum, created),
        )
        self.conn.commit()
        return Checkpoint(job_id, version, state, payload, checksum, created)

    def load_latest(self, job_id: str) -> Checkpoint | None:
        row = self.conn.execute(
            "SELECT * FROM checkpoints WHERE job_id=? ORDER BY version DESC LIMIT 1", (job_id,)
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row[3])
        expected = self._checksum(row[0], row[1], row[2], payload)
        if expected != row[4]:
            raise ValueError("checkpoint checksum mismatch")
        return Checkpoint(row[0], row[1], row[2], payload, row[4], row[5])
