"""skills.lock: what is installed, from which commit, under which license, and how far it's trusted.

JSON with sorted keys, so diffs stay small and reviews stay easy.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import LockfileError

LOCK_VERSION = 1
TRUST_LEVELS = ("quarantined", "indexed", "verified")
DEFAULT_LOCK = "skills.lock"


@dataclass(frozen=True)
class Approval:
    by: str
    at: str
    content_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {"at": self.at, "by": self.by, "content_sha256": self.content_sha256}


@dataclass(frozen=True)
class LockEntry:
    skill_id: str
    host: str  # github.com, or local
    repo: str
    path: str
    commit: str
    content_sha256: str
    license_spdx: str | None
    license_evidence: str
    commercial_use: str
    trust: str
    scan: dict[str, Any] = field(default_factory=dict)
    approval: Approval | None = None

    def __post_init__(self) -> None:
        if self.trust not in TRUST_LEVELS:
            raise LockfileError(f"{self.skill_id}: unknown trust level '{self.trust}'")

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval": self.approval.to_dict() if self.approval else None,
            "content_sha256": self.content_sha256,
            "license": {
                "commercial_use": self.commercial_use,
                "evidence": self.license_evidence,
                "spdx": self.license_spdx,
            },
            "scan": self.scan,
            "source": {"commit": self.commit, "host": self.host, "path": self.path, "repo": self.repo},
            "trust": self.trust,
        }

    @classmethod
    def from_dict(cls, skill_id: str, data: dict[str, Any]) -> LockEntry:
        try:
            source = data["source"]
            license_info = data["license"]
            approval = data.get("approval")
            return cls(
                skill_id=skill_id,
                host=source["host"],
                repo=source["repo"],
                path=source["path"],
                commit=source["commit"],
                content_sha256=data["content_sha256"],
                license_spdx=license_info["spdx"],
                license_evidence=license_info["evidence"],
                commercial_use=license_info["commercial_use"],
                trust=data["trust"],
                scan=data.get("scan") or {},
                approval=Approval(approval["by"], approval["at"], approval["content_sha256"]) if approval else None,
            )
        except (KeyError, TypeError) as error:
            raise LockfileError(f"{skill_id}: lock entry is missing {error}") from None


@dataclass
class Lockfile:
    path: Path
    entries: dict[str, LockEntry] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str = DEFAULT_LOCK) -> Lockfile:
        """A missing file is an empty lockfile. A damaged one is an error, and never treated as empty."""
        target = Path(path)
        if not target.exists():
            return cls(target)
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise LockfileError(f"{target} isn't valid JSON: {error}") from None
        if not isinstance(data, dict) or data.get("lock_version") != LOCK_VERSION:
            raise LockfileError(f"{target} has an unknown lock_version. This version reads {LOCK_VERSION}.")
        entries = {key: LockEntry.from_dict(key, value) for key, value in data.get("skills", {}).items()}
        return cls(target, entries)

    def add(self, entry: LockEntry) -> Lockfile:
        self.entries[entry.skill_id] = entry
        return self

    def get(self, skill_id: str) -> LockEntry:
        try:
            return self.entries[skill_id]
        except KeyError:
            raise LockfileError(f"'{skill_id}' isn't in {self.path}") from None

    def to_dict(self) -> dict[str, Any]:
        return {
            "lock_version": LOCK_VERSION,
            "skills": {key: self.entries[key].to_dict() for key in sorted(self.entries)},
        }

    def save(self) -> None:
        """Write to a temporary file and swap it in, so a crash can't leave half a lockfile."""
        text = json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        directory = self.path.resolve().parent
        directory.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=directory, prefix=".skills-lock-")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
                out.write(text)
            os.replace(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
