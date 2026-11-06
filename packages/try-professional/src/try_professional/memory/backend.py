"""The storage boundary. Mnemosyne is the implementation, and the gateway above it holds the rules."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .settings import MemorySettings, apply_to_environment, check_model_unchanged, verify_mnemosyne_is_local

SESSION = "trypro"


@dataclass(frozen=True)
class MemoryHit:
    text: str
    dense_score: float
    bank: str


class MemoryBackend(Protocol):
    def remember(self, bank: str, text: str, *, source: str) -> None: ...

    def recall(self, bank: str, query: str, top_k: int) -> list[MemoryHit]: ...


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
        self._local = threading.local()

    def _bank(self, bank: str) -> Any:
        from mnemosyne.core.memory import Mnemosyne

        instances: dict[str, Any] = self._local.__dict__.setdefault("instances", {})
        if bank not in instances:
            path: Path = self.settings.data_dir / "banks" / bank / "mnemosyne.db"
            path.parent.mkdir(parents=True, exist_ok=True)
            instances[bank] = Mnemosyne(session_id=SESSION, db_path=path, bank=bank)
        return instances[bank]

    def remember(self, bank: str, text: str, *, source: str) -> None:
        self._bank(bank).remember(text, source=source, scope="global", importance=0.5)

    def recall(self, bank: str, query: str, top_k: int) -> list[MemoryHit]:
        hits = self._bank(bank).recall(query, top_k=top_k)
        return [
            MemoryHit(text=str(h.get("content", "")), dense_score=float(h.get("dense_score") or 0.0), bank=bank)
            for h in hits
        ]
