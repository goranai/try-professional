"""Who can read and write which memory.

Roles read their department's bank and the company bank, and write only to their own
department's bank. Only the root role's department can promote into the company bank.
The role always comes from the runtime, and is never an argument a model can set.

This is policy, not a security boundary: see docs/architecture.md, section 13.
"""

from __future__ import annotations

from ..org import Org
from .backend import MemoryBackend, MemoryHit

COMPANY_BANK = "company"
DEFAULT_TOP_K = 5
MAX_TEXT_CHARS = 4000


class MemoryAccessDenied(PermissionError):
    """The role may not do this. The message is safe to show to the model."""


def department_bank(department: str) -> str:
    return f"dept-{department}"


class MemoryGateway:
    def __init__(
        self,
        org: Org,
        backend: MemoryBackend,
        *,
        floor: float = 0.0,
        top_k: int = DEFAULT_TOP_K,
    ) -> None:
        self.org = org
        self.backend = backend
        self.floor = floor
        self.top_k = top_k

    def _department_of(self, role: str) -> str:
        if not self.org.has_role(role):
            raise MemoryAccessDenied(f"'{role}' is not a role in this company")
        return role.split("/", 1)[0]

    def readable_banks(self, role: str) -> list[str]:
        return [department_bank(self._department_of(role)), COMPANY_BANK]

    def search(self, role: str, query: str) -> list[MemoryHit]:
        """Best matches across readable banks. The floor lets a query honestly find nothing."""
        hits: list[MemoryHit] = []
        for bank in self.readable_banks(role):
            hits += self.backend.recall(bank, query, self.top_k)
        kept = [h for h in hits if h.dense_score >= self.floor]
        kept.sort(key=lambda h: h.dense_score, reverse=True)
        return kept[: self.top_k]

    def recall(self, role: str, query: str) -> list[str]:
        return [f"[{hit.bank}] {hit.text}" for hit in self.search(role, query)]

    def _check_text(self, text: str) -> str:
        cleaned = text.strip()
        if not cleaned:
            raise MemoryAccessDenied("memory text is empty")
        if len(cleaned) > MAX_TEXT_CHARS:
            raise MemoryAccessDenied(f"memory text is over {MAX_TEXT_CHARS} characters. Save something shorter.")
        return cleaned

    def write(self, role: str, text: str) -> None:
        """Always the role's own department. There is no way to name another bank."""
        bank = department_bank(self._department_of(role))
        self.backend.remember(bank, self._check_text(text), source=role)

    def promote(self, role: str, text: str, source: str) -> None:
        """Make something company-wide. Only the root role's department can, with a source."""
        root_department = self.org.root_role.split("/", 1)[0]
        if self._department_of(role) != root_department:
            raise MemoryAccessDenied(f"Only {root_department} can write to company memory, not {role}.")
        if not source.strip():
            raise MemoryAccessDenied("A promotion needs a source reference.")
        self.backend.remember(COMPANY_BANK, self._check_text(text), source=f"{role} from {source}")
