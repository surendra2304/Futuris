from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class DurableStateStore:
    """Small WAL-backed store for resumable job/checkpoint metadata and outbox records."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS idempotency (
                    key TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    response_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    event_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    delivered_at TEXT
                );
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_job(
        self, job_id: str, tenant_id: str, principal_id: str, state: str, version: int, payload: dict[str, Any], updated_at: str
    ) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO jobs(job_id,tenant_id,principal_id,state,version,payload,updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(job_id) DO UPDATE SET
                    state=excluded.state,
                    version=excluded.version,
                    payload=excluded.payload,
                    updated_at=excluded.updated_at
                WHERE jobs.version = excluded.version - 1
                """,
                (job_id, tenant_id, principal_id, state, version, json.dumps(payload), updated_at),
            )
            return cur.rowcount == 1

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def append_outbox(self, event_id: str, tenant_id: str, event_type: str, payload: dict[str, Any]) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO outbox(event_id,tenant_id,event_type,payload) VALUES(?,?,?,?)",
                (event_id, tenant_id, event_type, json.dumps(payload)),
            )
            return cur.rowcount == 1

    def pending_outbox(self, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit outside allowed range")
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM outbox WHERE delivered_at IS NULL ORDER BY rowid LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_outbox_delivered(self, event_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE outbox SET delivered_at=datetime('now') WHERE event_id=? AND delivered_at IS NULL",
                (event_id,),
            )
            return cur.rowcount == 1
