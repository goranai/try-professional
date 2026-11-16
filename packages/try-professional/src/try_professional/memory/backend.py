"""The storage boundary. Mnemosyne is the implementation, and the gateway above it holds the rules."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from .embedding import FastEmbedEmbedder, Vectors
from .lessons import closest_lesson
from .settings import (
    MemorySettings,
    apply_to_environment,
    check_model_unchanged,
    share_the_model,
    verify_mnemosyne_is_local,
)

SESSION = "trypro"


@dataclass(frozen=True)
class MemoryHit:
    text: str
    dense_score: float
    bank: str


class MemoryBackend(Protocol):
    def remember(self, bank: str, text: str, *, source: str) -> None: ...

    def recall(self, bank: str, query: str, top_k: int) -> list[MemoryHit]: ...

    def size(self, bank: str) -> dict[str, int]: ...


def bank_size(data_dir: Path, bank: str) -> dict[str, int]:
    """Read from the file, so measuring a bank doesn't load the memory library or create the bank."""
    path = data_dir / "banks" / bank / "mnemosyne.db"
    if not path.is_file():
        return {"entries": 0, "bytes": 0}
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        entries = sum(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("working_memory", "episodic_memory")
            if table in tables
        )
    finally:
        connection.close()
    return {"entries": entries, "bytes": path.stat().st_size}


class MnemosyneBackend:
    """One SQLite file per bank.

    Each thread gets its own instance, because connections are per thread. Memories are
    stored with global scope, so a new session can recall what an earlier run learned.
    """

    def __init__(self, settings: MemorySettings) -> None:
        self.settings = settings
        apply_to_environment(settings)
        check_model_unchanged(settings)
        verify_mnemosyne_is_local(settings)
        share_the_model(settings)
        self.embedder = FastEmbedEmbedder(settings.embedding_model, settings.cache_dir)
        self._local = threading.local()

    def _bank(self, bank: str) -> Any:
        from mnemosyne.core.memory import Mnemosyne

        instances: dict[str, Any] = self._local.__dict__.setdefault("instances", {})
        if bank not in instances:
            path: Path = self.settings.data_dir / "banks" / bank / "mnemosyne.db"
            path.parent.mkdir(parents=True, exist_ok=True)
            instances[bank] = Mnemosyne(session_id=SESSION, db_path=path, bank=bank)
        return instances[bank]

    def size(self, bank: str) -> dict[str, int]:
        return bank_size(self.settings.data_dir, bank)

    def remember(self, bank: str, text: str, *, source: str) -> None:
        from mnemosyne.core.filters import admit_memory_write
        from mnemosyne.core.memory import _deferred_commits

        if not admit_memory_write(text)[0]:
            return
        memory = self._bank(bank)
        vector = self.embedder.embed_documents([text])[0]
        # Recall mixes lexical and dense ranking. Compare stored document vectors
        # directly instead, so a crowded recall page cannot hide the closest lesson.
        # Mnemosyne also commits internally. Its transaction scope keeps the
        # candidate read and either write atomic across threads and processes.
        with _deferred_commits(memory.conn, immediate=True):
            rows = memory.conn.execute(
                "SELECT w.id, w.content, w.source, w.metadata_json, e.embedding_json, e.model, 'working_memory' "
                "FROM working_memory w LEFT JOIN memory_embeddings e ON e.memory_id = w.id "
                "WHERE w.scope = 'global' AND w.superseded_by IS NULL "
                "UNION ALL "
                "SELECT w.id, w.content, w.source, w.metadata_json, e.embedding_json, e.model, 'episodic_memory' "
                "FROM episodic_memory w LEFT JOIN memory_embeddings e ON e.memory_id = w.id "
                "WHERE w.scope = 'global' AND w.superseded_by IS NULL ORDER BY id"
            ).fetchall()
            match = next((i for i, row in enumerate(rows) if row[1] == text), None)
            if match is None:
                candidates: list[Vectors] = []
                for row in rows:
                    if row[4] is not None and row[5] == self.embedder.model_name:
                        embedded = np.asarray(json.loads(row[4]), dtype=np.float32)
                    else:
                        embedded = self.embedder.embed_documents([row[1]])[0]
                    # Mnemosyne's stored vectors need not already be normalized.
                    norm = np.linalg.norm(embedded)
                    candidates.append(np.asarray(embedded / norm if norm else embedded, dtype=np.float32))
                match = closest_lesson(vector, candidates, self.embedder.model_name)
            evidence = {"text": text, "source": source}
            if match is None:
                metadata = {"trypro_lesson": {"learned_count": 1, "evidence": [evidence]}}
            else:
                row = rows[match]
                metadata = json.loads(row[3] or "{}")
                lesson = metadata.setdefault(
                    "trypro_lesson", {"learned_count": 1, "evidence": [{"text": row[1], "source": row[2]}]}
                )
                lesson["learned_count"] += 1
                lesson["evidence"].append(evidence)
                encoded = json.dumps(metadata)
                memory.conn.execute(f"UPDATE {row[6]} SET metadata_json = ? WHERE id = ?", (encoded, row[0]))
                memory.conn.execute("UPDATE memories SET metadata_json = ? WHERE id = ?", (encoded, row[0]))
                return
            # The merge only updates evidence, never the canonical words, their
            # vector, or the original source. New memories use Mnemosyne's facade.
            memory.remember(text, source=source, scope="global", importance=0.5, metadata=metadata)

    def recall(self, bank: str, query: str, top_k: int) -> list[MemoryHit]:
        hits = self._bank(bank).recall(query, top_k=top_k)
        return [
            MemoryHit(text=str(h.get("content", "")), dense_score=float(h.get("dense_score") or 0.0), bank=bank)
            for h in hits
        ]
