"""Who can read and write which memory. There are three tiers.

- Company: vision, rules, major decisions. All roles read it. The root role proposes, and a person
  approves. `promote` is how an approved change is written, and no tool gives an agent access to it.
- Department: team practices and shared lessons. The department reads it, and only its head writes.
- Personal: one role's own notes, lessons, and mistakes. Only that role reads or writes it.

The role always comes from the runtime, and is never an argument a model can set.

This is policy, not a security boundary: see docs/architecture.md, section 13.
"""

from __future__ import annotations

from collections.abc import Callable

from ..org import Org
from .backend import MemoryBackend, MemoryHit

COMPANY_BANK = "company"
DEFAULT_TOP_K = 7
# How many of the best matches each tier may contribute. A prompt never gets a whole bank.
PERSONAL_HITS = 3
DEPARTMENT_HITS = 2
COMPANY_HITS = 2
MAX_HIT_CHARS = 300
MAX_TEXT_CHARS = 4000


class MemoryAccessDenied(PermissionError):
    """The role may not do this. The message is safe to show to the model."""


def department_bank(department: str) -> str:
    return f"dept-{department}"


def personal_bank(role: str) -> str:
    return "agent-" + role.replace("/", "--")


def _label(bank: str) -> str:
    if bank.startswith("agent-"):
        return "personal"
    return bank


def tier_sizes(org: Org, size: Callable[[str], dict[str, int]]) -> dict[str, dict[str, int]]:
    """Entries and bytes per tier, for `trypro status`."""
    tiers = {
        "company": [COMPANY_BANK],
        "department": [department_bank(d) for d in org.departments],
        "personal": [personal_bank(r) for r in org.roles],
    }
    totals: dict[str, dict[str, int]] = {}
    for tier, banks in tiers.items():
        sizes = [size(bank) for bank in banks]
        totals[tier] = {
            "entries": sum(s["entries"] for s in sizes),
            "bytes": sum(s["bytes"] for s in sizes),
        }
    return totals


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
        return [personal_bank(role), department_bank(self._department_of(role)), COMPANY_BANK]

    def _from_bank(self, bank: str, query: str, limit: int) -> list[MemoryHit]:
        hits = [h for h in self.backend.recall(bank, query, limit) if h.dense_score >= self.floor]
        hits.sort(key=lambda h: h.dense_score, reverse=True)
        return hits[:limit]

    def search(self, role: str, query: str) -> list[MemoryHit]:
        """Best matches, with a limit per tier. The floor lets a query honestly find nothing."""
        limits = (PERSONAL_HITS, DEPARTMENT_HITS, COMPANY_HITS)
        hits: list[MemoryHit] = []
        for bank, limit in zip(self.readable_banks(role), limits, strict=True):
            hits += self._from_bank(bank, query, limit)
        hits.sort(key=lambda h: h.dense_score, reverse=True)
        return hits[: self.top_k]

    def recall(self, role: str, query: str) -> list[str]:
        return [f"[{_label(hit.bank)}] {hit.text[:MAX_HIT_CHARS]}" for hit in self.search(role, query)]

    def recall_personal(self, role: str, query: str) -> list[str]:
        self._department_of(role)
        return [hit.text[:MAX_HIT_CHARS] for hit in self._from_bank(personal_bank(role), query, PERSONAL_HITS)]

    def _check_text(self, text: str) -> str:
        cleaned = text.strip()
        if not cleaned:
            raise MemoryAccessDenied("memory text is empty")
        if len(cleaned) > MAX_TEXT_CHARS:
            raise MemoryAccessDenied(f"memory text is over {MAX_TEXT_CHARS} characters. Save something shorter.")
        return cleaned

    def write(self, role: str, text: str) -> None:
        """Always the role's own personal memory. There is no way to name another bank."""
        self._department_of(role)
        self.backend.remember(personal_bank(role), self._check_text(text), source=role)

    def write_department(self, role: str, text: str) -> None:
        """Only a department's head curates its memory, and only its own department's."""
        department = self._department_of(role)
        if not self.org.is_head(role):
            raise MemoryAccessDenied(f"Only the head of {department} can change its memory, not {role}.")
        self.backend.remember(department_bank(department), self._check_text(text), source=role)

    def promote(self, role: str, text: str, source: str) -> None:
        """Write an approved change to company memory. No agent tool reaches this."""
        root_department = self.org.root_role.split("/", 1)[0]
        if self._department_of(role) != root_department:
            raise MemoryAccessDenied(f"Only {root_department} can write to company memory, not {role}.")
        if not source.strip():
            raise MemoryAccessDenied("A promotion needs a source reference.")
        self.backend.remember(COMPANY_BANK, self._check_text(text), source=f"{role} from {source}")

    def sizes(self) -> dict[str, dict[str, int]]:
        return tier_sizes(self.org, self.backend.size)
