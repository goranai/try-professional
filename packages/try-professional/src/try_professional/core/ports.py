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

    def loadout(self, role: str) -> list[dict[str, Any]]: ...

    def available(self, role: str) -> bool: ...

    def proposable(self, role: str) -> bool: ...

    def propose(self, proposer: str, skill_id: str, for_role: str, reason: str) -> None: ...


class FeedbackPort(Protocol):
    """How skills did. Used to re-rank loadouts, and to spot gaps and unused skills."""

    def note_search(self, run_id: str, task_id: str, role: str, query: str, found: list[dict[str, Any]]) -> None: ...

    def skill_used(self, run_id: str, task_id: str, role: str, skill_id: str) -> None: ...

    def qa_outcome(self, run_id: str, task_id: str, *, passed: bool) -> None: ...

    def run_finished(self, run_id: str, roles: list[str]) -> None: ...


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

    def loadout(self, role: str) -> list[dict[str, Any]]:
        return []

    def available(self, role: str) -> bool:
        return False

    def proposable(self, role: str) -> bool:
        return False

    def propose(self, proposer: str, skill_id: str, for_role: str, reason: str) -> None:
        raise PermissionError("No skills are installed")
