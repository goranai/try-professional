"""Durable run state in SQLite. State is saved after every event, so runs can resume."""

from __future__ import annotations

import gzip
import sqlite3
import threading
from pathlib import Path

from .pipeline.state import RunState

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id     TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    audit_head TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Ledger:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._root = path.parent
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def save(self, state: RunState, audit_head: str, timestamp: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (run_id, state_json, audit_head, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET state_json=excluded.state_json, "
                "audit_head=excluded.audit_head, updated_at=excluded.updated_at",
                (state.run_id, state.model_dump_json(), audit_head, timestamp),
            )
            self._conn.commit()

    def load(self, run_id: str) -> RunState:
        with self._lock:
            row = self._conn.execute("SELECT state_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown run '{run_id}'")
        if not row[0]:
            return RunState.model_validate_json(gzip.decompress(self._archive_path(run_id).read_bytes()))
        return RunState.model_validate_json(row[0])

    def _archive_path(self, run_id: str) -> Path:
        return self._root / "runs" / run_id / "state.json.gz"

    def archive(self, run_id: str) -> None:
        """Move a finished run's state out of the database, so it doesn't grow forever. It still loads."""
        state = self.load(run_id)
        path = self._archive_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(state.model_dump_json().encode("utf-8")))
        with self._lock:
            self._conn.execute("UPDATE runs SET state_json = '' WHERE run_id = ?", (run_id,))
            self._conn.commit()

    def is_archived(self, run_id: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT state_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return row is not None and not row[0]

    def audit_head(self, run_id: str) -> str:
        with self._lock:
            row = self._conn.execute("SELECT audit_head FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown run '{run_id}'")
        return str(row[0])

    def list_runs(self) -> list[tuple[str, str]]:
        with self._lock:
            rows = self._conn.execute("SELECT run_id, updated_at FROM runs ORDER BY updated_at DESC").fetchall()
        return [(str(r[0]), str(r[1])) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
