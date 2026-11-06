"""What the orchestrator needs from memory and skills.

Phases 3 and 4 provide the real implementations. The null versions make a run
work with neither installed.
"""

from __future__ import annotations

from typing import Any, Protocol


class MemoryPort(Protocol):
    def recall(self, role: str, query: str) -> list[str]: ...

    def write(self, role: str, text: str) -> None: ...

    def promote(self, role: str, text: str, source: str) -> None: ...


class SkillPort(Protocol):
    def search(self, role: str, query: str) -> list[dict[str, Any]]: ...

    def get(self, role: str, skill_id: str) -> str | None: ...


class ProposalPort(Protocol):
    def propose(self, text: str, *, source: str) -> str: ...


class NullMemory:
    def recall(self, role: str, query: str) -> list[str]:
        return []

    def write(self, role: str, text: str) -> None:
        return None

    def promote(self, role: str, text: str, source: str) -> None:
        return None


class NullSkills:
    def search(self, role: str, query: str) -> list[dict[str, Any]]:
        return []

    def get(self, role: str, skill_id: str) -> str | None:
        return None
