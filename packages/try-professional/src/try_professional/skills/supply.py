"""Connects skill-customs to agents. It uses only that package's public API, like any other user.

Approving a skill isn't here on purpose. Agents can search and read what a person's lockfile
allows, and nothing in this package can change a skill's trust level.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from skill_customs import Lockfile, read_skill_metadata, skill_dir

from ..org import Org
from .catalog import GENERAL_CATEGORY, CatalogSkills, SkillCatalog, SkillRecord

MAX_SKILL_CHARS = 60_000


def records_from_lock(lock: Lockfile, dest: Path) -> list[SkillRecord]:
    """Catalog rows from skills.lock. Quarantined skills get rows too, and search never returns them."""
    records = []
    for skill_id, entry in sorted(lock.entries.items()):
        metadata = read_skill_metadata(skill_dir(entry, dest))
        records.append(
            SkillRecord(
                skill_id=skill_id,
                name=metadata.name,
                description=metadata.description,
                category=metadata.category or GENERAL_CATEGORY,
                source_repo=entry.repo,
                commit_sha=entry.commit,
                license=entry.license_spdx or "",
                trust=entry.trust,
            )
        )
    return records


def sync_catalog(catalog: SkillCatalog, lock: Lockfile, dest: Path) -> int:
    records = records_from_lock(lock, dest)
    catalog.rebuild(records)
    return len(records)


class LockedSkills:
    """What a role can reach: search plus reading one skill, limited by trust and category."""

    def __init__(self, org: Org, catalog: SkillCatalog, dest: Path, lock: Lockfile) -> None:
        self.org = org
        self.catalog = catalog
        self.dest = dest
        self.lock = lock
        self._search = CatalogSkills(org, catalog)

    def search(self, role: str, query: str) -> list[dict[str, Any]]:
        return self._search.search(role, query)

    def get(self, role: str, skill_id: str) -> str | None:
        """The skill's text, or None if this role may not have it. The caller labels it untrusted."""
        record = self.catalog.record(skill_id)
        if record is None or record.trust == "quarantined":
            return None
        allowed = (*self.org.role(role).allowed_skill_categories, GENERAL_CATEGORY)
        if record.category not in allowed or skill_id not in self.lock.entries:
            return None
        path = skill_dir(self.lock.entries[skill_id], self.dest) / "SKILL.md"
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")[:MAX_SKILL_CHARS]
