"""Connects skill-customs and loadouts to agents, using only skill-customs' public API.

Nothing here changes a skill's trust, or moves one into a loadout. Agents can read what a
person's decisions allow, and a department head can propose. Everything else is a person's job.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skill_customs import Lockfile, read_skill_metadata, skill_dir

from ..org import Org
from .catalog import UNCATEGORIZED, SkillCatalog, SkillRecord
from .loadouts import GAP_THRESHOLD, SHELL, SHORT_DESCRIPTION, Decisions, LoadoutStore, compute_loadouts

MAX_SKILL_CHARS = 60_000
_UNCATEGORIZED_LABEL = "[uncategorized: no person has assigned this skill a category yet]\n"


def records_from_lock(lock: Lockfile, dest: Path, decisions: Decisions | None = None) -> list[SkillRecord]:
    """Catalog rows from skills.lock. A person's category wins over what the skill says about itself."""
    records = []
    for skill_id, entry in sorted(lock.entries.items()):
        metadata = read_skill_metadata(skill_dir(entry, dest))
        assigned = decisions.category_for(skill_id) if decisions else None
        tools = list(metadata.tools)
        # A skill that ships scripts is only useful to a role that can run them.
        if entry.scan.get("executable_files"):
            tools.append(SHELL)
        records.append(
            SkillRecord(
                skill_id=skill_id,
                name=metadata.name,
                description=metadata.description,
                category=assigned or metadata.category or UNCATEGORIZED,
                source_repo=entry.repo,
                commit_sha=entry.commit,
                license=entry.license_spdx or "",
                trust=entry.trust,
                tools=tuple(dict.fromkeys(tools)),
                content_sha256=entry.content_sha256,
            )
        )
    return records


def sync_catalog(catalog: SkillCatalog, lock: Lockfile, dest: Path, decisions: Decisions | None = None) -> int:
    records = records_from_lock(lock, dest, decisions)
    catalog.rebuild(records)
    return len(records)


def match_all(org: Org, catalog: SkillCatalog, store: LoadoutStore, decisions: Decisions) -> dict[str, Any]:
    """Recompute every loadout, using the feedback gathered so far."""
    loadouts = compute_loadouts(org, catalog.eligible(), catalog.embedder, decisions, store.usage)
    store.replace_matches(loadouts)
    return loadouts


class LockedSkills:
    """What a role can reach. A role sees its approved loadout and nothing else.

    The root role and department heads also see uncategorized skills, labelled, so they can
    propose them. No other role sees those until a person assigns a category.
    """

    def __init__(self, org: Org, catalog: SkillCatalog, dest: Path, lock: Lockfile, store: LoadoutStore) -> None:
        self.org = org
        self.catalog = catalog
        self.dest = dest
        self.lock = lock
        self.store = store

    def _uncategorized(self) -> set[str]:
        return {record.skill_id for record, _ in self.catalog.eligible() if record.category == UNCATEGORIZED}

    def _active(self, role: str) -> set[str]:
        return {item["skill_id"] for item in self.store.section(role, "active")}

    def _reachable(self, role: str) -> set[str]:
        reachable = self._active(role)
        if self.org.is_senior(role):
            reachable |= self._uncategorized()
        return reachable

    def loadout(self, role: str) -> list[dict[str, Any]]:
        """Names and short descriptions only. An agent fetches the full skill when it needs it."""
        return [
            {"skill_id": item["skill_id"], "description": item["description"]}
            for item in self.store.section(role, "active")
        ]

    def available(self, role: str) -> bool:
        return bool(self._reachable(role))

    def proposable(self, role: str) -> bool:
        """Only senior roles propose, and only if there's something to propose."""
        return self.org.is_senior(role) and any(record for record, _ in self.catalog.eligible())

    def search(self, role: str, query: str) -> list[dict[str, Any]]:
        reachable = self._reachable(role)
        if not reachable:
            return []
        hits = self.catalog.search(query, skill_ids=reachable)
        return [
            {
                "skill_id": h.record.skill_id,
                "score": round(h.score, 4),
                "trust": h.record.trust,
                "category": h.record.category,
                "description": h.record.description[:SHORT_DESCRIPTION],
            }
            for h in hits
        ]

    def get(self, role: str, skill_id: str) -> str | None:
        """The skill's text, or None if this role may not have it. The caller labels it untrusted."""
        if skill_id not in self._reachable(role) or skill_id not in self.lock.entries:
            return None
        record = self.catalog.record(skill_id)
        if record is None or record.trust == "quarantined":
            return None
        path = skill_dir(self.lock.entries[skill_id], self.dest) / "SKILL.md"
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8", errors="replace")[:MAX_SKILL_CHARS]
        return _UNCATEGORIZED_LABEL + text if record.category == UNCATEGORIZED else text

    def propose(self, proposer: str, skill_id: str, for_role: str, reason: str) -> None:
        """Ask a person to admit a skill. It changes nothing until they do."""
        if not self.org.is_senior(proposer):
            raise PermissionError(f"{proposer} can't propose skills. Department heads can.")
        if not self.org.has_role(for_role):
            raise PermissionError(f"'{for_role}' isn't a role in this company")
        if proposer != self.org.root_role and for_role.split("/", 1)[0] != proposer.split("/", 1)[0]:
            raise PermissionError(f"{proposer} can only propose for its own department")
        record = self.catalog.record(skill_id)
        if record is None or record.trust == "quarantined":
            raise PermissionError(f"'{skill_id}' doesn't exist, or isn't available")
        self.store.propose(for_role, skill_id, by=proposer, reason=reason)

    # ---- feedback, called by the orchestrator ---------------------------------------

    def note_search(self, run_id: str, task_id: str, role: str, query: str, found: list[dict[str, Any]]) -> None:
        best = max((float(item["score"]) for item in found), default=0.0)
        if best < GAP_THRESHOLD:
            self.store.record_gap(role, query, best, run_id=run_id, task_id=task_id)

    def skill_used(self, run_id: str, task_id: str, role: str, skill_id: str) -> None:
        self.store.record_use(run_id, task_id, role, skill_id)

    def qa_outcome(self, run_id: str, task_id: str, *, passed: bool) -> None:
        self.store.record_outcome(run_id, task_id, passed=passed)

    def run_finished(self, run_id: str, roles: list[str]) -> None:
        self.store.record_run(run_id, roles)
