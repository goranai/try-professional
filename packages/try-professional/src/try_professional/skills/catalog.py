"""The skill catalog: SQLite for structured fields, and plain cosine for ranking.

It isn't a memory bank. Entries must not expire or change when read, ranking must be
deterministic, and license and trust are columns to filter on. It holds no state of
its own: it can always be rebuilt from skills.lock. See docs/architecture.md, section 8.5.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..memory.embedding import Embedder
from ..org import Org

TRUST_LEVELS = ("verified", "indexed", "quarantined")
# Quarantined skills are never offered to an agent, whatever a caller asks for.
SEARCHABLE_TRUST = ("verified", "indexed")
DEFAULT_TOP_K = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS skills (
    skill_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL,
    category    TEXT NOT NULL,
    departments TEXT NOT NULL,
    source_repo TEXT NOT NULL,
    commit_sha  TEXT NOT NULL,
    license     TEXT NOT NULL,
    trust       TEXT NOT NULL,
    vector      BLOB NOT NULL
);
"""


class CatalogModelMismatch(RuntimeError):
    """The catalog was built with another model, and its vectors can't be compared."""


@dataclass(frozen=True)
class SkillRecord:
    skill_id: str
    name: str
    description: str
    category: str
    departments: tuple[str, ...] = ()
    source_repo: str = ""
    commit_sha: str = ""
    license: str = ""
    trust: str = "indexed"

    def __post_init__(self) -> None:
        if self.trust not in TRUST_LEVELS:
            raise ValueError(f"unknown trust level '{self.trust}'")

    @property
    def search_text(self) -> str:
        return f"{self.name}: {self.description}"


@dataclass(frozen=True)
class SkillHit:
    record: SkillRecord
    score: float = field(default=0.0)


class SkillCatalog:
    def __init__(self, path: Path, embedder: Embedder) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        row = self._conn.execute("SELECT value FROM meta WHERE key = 'model'").fetchone()
        count = self._conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
        if row and row[0] != embedder.model_name and count:
            raise CatalogModelMismatch(
                f"This catalog was built with '{row[0]}', not '{embedder.model_name}'. Rebuild it from skills.lock."
            )

    def rebuild(self, records: list[SkillRecord]) -> None:
        """Replace everything in one transaction, so a failure leaves the old catalog intact."""
        vectors = self.embedder.embed_documents([r.search_text for r in records])
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM skills")
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('model', ?)", (self.embedder.model_name,)
            )
            self._conn.executemany(
                "INSERT INTO skills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        r.skill_id,
                        r.name,
                        r.description,
                        r.category,
                        json.dumps(list(r.departments)),
                        r.source_repo,
                        r.commit_sha,
                        r.license,
                        r.trust,
                        vector.astype(np.float32).tobytes(),
                    )
                    for r, vector in zip(records, vectors, strict=True)
                ],
            )

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0])

    def search(
        self,
        query: str,
        *,
        categories: tuple[str, ...] | None = None,
        top_k: int = DEFAULT_TOP_K,
    ) -> list[SkillHit]:
        """Filter on structured columns first, then rank by cosine. Ties break by ID, so it's repeatable.

        There is deliberately no score floor. A weak suggestion costs little, since it carries its
        score and an agent can ignore it, while a skill filtered out is lost for good. Measured in
        docs/research/phase3-retrieval/.
        """
        placeholders = ",".join("?" for _ in SEARCHABLE_TRUST)
        sql = (
            "SELECT skill_id, name, description, category, departments, source_repo, commit_sha, license, trust, "
            f"vector FROM skills WHERE trust IN ({placeholders})"
        )
        params: list[Any] = list(SEARCHABLE_TRUST)
        if categories is not None:
            if not categories:
                return []
            sql += f" AND category IN ({','.join('?' for _ in categories)})"
            params += list(categories)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        if not rows:
            return []
        query_vector = self.embedder.embed_query(query)
        matrix = np.vstack([np.frombuffer(row[9], dtype=np.float32) for row in rows])
        scores = matrix @ query_vector
        ranked = sorted(zip(scores.tolist(), rows, strict=True), key=lambda item: (-item[0], item[1][0]))
        hits = []
        for score, row in ranked[:top_k]:
            record = SkillRecord(
                skill_id=row[0],
                name=row[1],
                description=row[2],
                category=row[3],
                departments=tuple(json.loads(row[4])),
                source_repo=row[5],
                commit_sha=row[6],
                license=row[7],
                trust=row[8],
            )
            hits.append(SkillHit(record, float(score)))
        return hits

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class CatalogSkills:
    """Skill search for a role: limited to the categories its role file allows."""

    def __init__(self, org: Org, catalog: SkillCatalog) -> None:
        self.org = org
        self.catalog = catalog

    def search(self, role: str, query: str) -> list[dict[str, Any]]:
        categories = self.org.role(role).allowed_skill_categories
        hits = self.catalog.search(query, categories=categories)
        return [{"skill_id": h.record.skill_id, "score": round(h.score, 4), "trust": h.record.trust} for h in hits]


__all__ = ["CatalogModelMismatch", "CatalogSkills", "SkillCatalog", "SkillHit", "SkillRecord"]
