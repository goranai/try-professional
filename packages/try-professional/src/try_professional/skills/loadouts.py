"""Which skills fit which role.

Every eligible skill is scored against every role: embedding similarity to the role's
skill_needs and responsibilities, plus category fit, plus what past use showed. Skills needing
tools the role can't have are excluded. Near-duplicates collapse. Only a person can move a
skill into a loadout, and a department head can only propose one.

Two stores, on purpose. Human decisions go in skills/decisions.yaml, which is meant for git and
which no agent code path writes. Computed matches, proposals, usage, and gaps go in SQLite.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ..memory.embedding import Embedder
from ..org import Org, Role
from .catalog import UNCATEGORIZED, SkillRecord

# Chosen on real skills and the fixture's development half, then scored on separate data.
# A higher value tuned on the fixture alone suggested nothing on real skills. See docs/research/loadouts/.
MATCH_THRESHOLD = 0.44
# A skill is suggested only to the roles it fits best: at most this many, and only those close
# to its best score. Without this, generic wording pulls a skill into unrelated roles.
MAX_ROLES_PER_SKILL = 4
ROLE_MARGIN = 0.10
# skill_needs are written to describe skills. Responsibilities describe the job, so they count less.
RESPONSIBILITY_WEIGHT = 0.9
# Category is stronger evidence than wording, because a person assigned it, or at least
# a skill's author chose it. Real skills showed wording alone isn't enough: code review
# matched Compliance and Sales, and only a category separates those.
CATEGORY_BONUS = 0.08
CATEGORY_PENALTY = 0.10
FEEDBACK_WEIGHT = 0.05
FEEDBACK_MIN_USES = 3
DUPLICATE_SIMILARITY = 0.92
# A task whose best skill scores below this is logged as a gap. Query scores run lower
# than need scores, because a task is phrased less like a skill than a need is.
GAP_THRESHOLD = 0.40
DEFAULT_UNUSED_RUNS = 20
SHORT_DESCRIPTION = 70

# What skill files call their tools, mapped to what roles are allowed. Anything unknown is
# treated as a tool the role lacks, so an unfamiliar skill is excluded and not assumed safe.
TOOL_ALIASES = {
    "read": "workspace_read",
    "grep": "workspace_read",
    "view": "workspace_read",
    "glob": "workspace_list",
    "ls": "workspace_list",
    "write": "workspace_write",
    "edit": "workspace_write",
    "multiedit": "workspace_write",
    "bash": "shell",
    "shell": "shell",
    "terminal": "shell",
    "execute_code": "shell",
    "webfetch": "network",
    "websearch": "network",
    "web_search": "network",
    "browser": "network",
}
SHELL = "shell"


def normalize_tool(name: str) -> str:
    """Bash(git status:*) and bash both mean shell."""
    base = name.split("(", 1)[0].strip().lower()
    return TOOL_ALIASES.get(base, name.split("(", 1)[0].strip())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Decisions:
    """What people decided. Only the trypro command for people writes this file."""

    VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = path
        data: dict[str, Any] = {}
        if path.is_file():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if data.get("version") != self.VERSION:
                raise ValueError(f"{path} has an unknown version")
        self.categories: dict[str, dict[str, str]] = data.get("categories") or {}
        self.loadouts: dict[str, dict[str, dict[str, str]]] = data.get("loadouts") or {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = {"version": self.VERSION, "categories": self.categories, "loadouts": self.loadouts}
        text = yaml.safe_dump(document, sort_keys=True, allow_unicode=True)
        self.path.write_text(text, encoding="utf-8", newline="\n")

    def category_for(self, skill_id: str) -> str | None:
        decision = self.categories.get(skill_id)
        return decision["category"] if decision else None

    def assign_category(self, skill_id: str, category: str, *, by: str) -> None:
        self.categories[skill_id] = {"category": category, "by": by, "at": _now()}

    def decide(self, role: str, skill_id: str, status: str, *, by: str, content_sha256: str) -> None:
        self.loadouts.setdefault(role, {})[skill_id] = {
            "status": status,
            "by": by,
            "at": _now(),
            "content_sha256": content_sha256,
        }

    def status(self, role: str, record: SkillRecord) -> str:
        """approved, rejected, or pending. A change to the skill's content voids an earlier decision."""
        decision = self.loadouts.get(role, {}).get(record.skill_id)
        if decision is None or decision.get("content_sha256") != record.content_sha256:
            return "pending"
        return decision["status"]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    role TEXT NOT NULL, skill_id TEXT NOT NULL, section TEXT NOT NULL, rank INTEGER NOT NULL,
    score REAL NOT NULL, why TEXT NOT NULL, description TEXT NOT NULL, category TEXT NOT NULL,
    PRIMARY KEY (role, skill_id)
);
CREATE TABLE IF NOT EXISTS proposals (
    role TEXT NOT NULL, skill_id TEXT NOT NULL, proposed_by TEXT NOT NULL, reason TEXT NOT NULL, at TEXT NOT NULL,
    PRIMARY KEY (role, skill_id)
);
CREATE TABLE IF NOT EXISTS usage (
    role TEXT NOT NULL, skill_id TEXT NOT NULL, uses INTEGER NOT NULL DEFAULT 0,
    passed INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0, idle_runs INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (role, skill_id)
);
CREATE TABLE IF NOT EXISTS uses (
    run_id TEXT NOT NULL, task_id TEXT NOT NULL, role TEXT NOT NULL, skill_id TEXT NOT NULL, outcome TEXT,
    PRIMARY KEY (run_id, task_id, role, skill_id)
);
CREATE TABLE IF NOT EXISTS gaps (
    role TEXT NOT NULL, description TEXT NOT NULL, best_score REAL NOT NULL, count INTEGER NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, run_id TEXT NOT NULL, task_id TEXT NOT NULL,
    PRIMARY KEY (role, description)
);
"""


class LoadoutStore:
    """Computed loadouts, proposals, usage, and gaps. Rebuildable, apart from the feedback history."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- computed loadouts ---------------------------------------------------------

    def replace_matches(self, loadouts: dict[str, RoleLoadout]) -> None:
        rows = []
        for role, loadout in loadouts.items():
            for section, matches in (
                ("active", loadout.active),
                ("pending", loadout.pending),
                ("over_cap", loadout.over_cap),
            ):
                for rank, match in enumerate(matches, start=1):
                    description = match.record.description[:SHORT_DESCRIPTION]
                    rows.append(
                        (
                            role,
                            match.record.skill_id,
                            section,
                            rank,
                            match.score,
                            match.why,
                            description,
                            match.record.category,
                        )
                    )
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM matches")
            self._conn.executemany("INSERT INTO matches VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)

    def section(self, role: str, section: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT skill_id, rank, score, why, description, category FROM matches "
                "WHERE role = ? AND section = ? ORDER BY rank",
                (role, section),
            ).fetchall()
        return [
            {
                "skill_id": r[0],
                "rank": r[1],
                "score": round(r[2], 3),
                "why": r[3],
                "description": r[4],
                "category": r[5],
            }
            for r in rows
        ]

    # ---- proposals: agents write these, and they change nothing until a person acts --

    def propose(self, role: str, skill_id: str, *, by: str, reason: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO proposals VALUES (?, ?, ?, ?, ?)", (role, skill_id, by, reason[:300], _now())
            )

    def proposals(self, role: str | None = None) -> list[dict[str, str]]:
        sql = "SELECT role, skill_id, proposed_by, reason, at FROM proposals"
        with self._lock:
            rows = (
                self._conn.execute(f"{sql} WHERE role = ? ORDER BY at", (role,)).fetchall()
                if role
                else self._conn.execute(f"{sql} ORDER BY at").fetchall()
            )
        return [{"role": r[0], "skill_id": r[1], "proposed_by": r[2], "reason": r[3], "at": r[4]} for r in rows]

    # ---- feedback -------------------------------------------------------------------

    def record_use(self, run_id: str, task_id: str, role: str, skill_id: str) -> None:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO uses VALUES (?, ?, ?, ?, NULL)", (run_id, task_id, role, skill_id)
            )
            if cursor.rowcount:
                self._conn.execute(
                    "INSERT INTO usage (role, skill_id, uses) VALUES (?, ?, 1) "
                    "ON CONFLICT(role, skill_id) DO UPDATE SET uses = uses + 1",
                    (role, skill_id),
                )

    def record_outcome(self, run_id: str, task_id: str, *, passed: bool) -> None:
        """A task's latest QA verdict decides, so rework that ends in approval counts as a pass."""
        column = "passed" if passed else "failed"
        outcome = "passed" if passed else "failed"
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT role, skill_id, outcome FROM uses WHERE run_id = ? AND task_id = ?", (run_id, task_id)
            ).fetchall()
            for role, skill_id, previous in rows:
                if previous == outcome:
                    continue
                if previous:
                    self._conn.execute(
                        f"UPDATE usage SET {previous} = {previous} - 1 WHERE role = ? AND skill_id = ?",
                        (role, skill_id),
                    )
                self._conn.execute(
                    f"UPDATE usage SET {column} = {column} + 1 WHERE role = ? AND skill_id = ?", (role, skill_id)
                )
                self._conn.execute(
                    "UPDATE uses SET outcome = ? WHERE run_id = ? AND task_id = ? AND role = ? AND skill_id = ?",
                    (outcome, run_id, task_id, role, skill_id),
                )

    def record_run(self, run_id: str, roles: list[str]) -> None:
        """Each role that worked in this run: reset idle counts for skills it used, and bump the rest."""
        with self._lock, self._conn:
            for role in roles:
                used = {
                    row[0]
                    for row in self._conn.execute(
                        "SELECT skill_id FROM uses WHERE run_id = ? AND role = ?", (run_id, role)
                    )
                }
                active = [
                    row[0]
                    for row in self._conn.execute(
                        "SELECT skill_id FROM matches WHERE role = ? AND section = 'active'", (role,)
                    )
                ]
                for skill_id in active:
                    self._conn.execute(
                        "INSERT INTO usage (role, skill_id) VALUES (?, ?) ON CONFLICT(role, skill_id) DO NOTHING",
                        (role, skill_id),
                    )
                    if skill_id in used:
                        self._conn.execute(
                            "UPDATE usage SET idle_runs = 0 WHERE role = ? AND skill_id = ?", (role, skill_id)
                        )
                    else:
                        self._conn.execute(
                            "UPDATE usage SET idle_runs = idle_runs + 1 WHERE role = ? AND skill_id = ?",
                            (role, skill_id),
                        )

    def usage(self, role: str, skill_id: str) -> dict[str, int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT uses, passed, failed, idle_runs FROM usage WHERE role = ? AND skill_id = ?", (role, skill_id)
            ).fetchone()
        values = row or (0, 0, 0, 0)
        return {"uses": values[0], "passed": values[1], "failed": values[2], "idle_runs": values[3]}

    def unused(self, threshold: int = DEFAULT_UNUSED_RUNS) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, skill_id, idle_runs FROM usage WHERE idle_runs >= ? "
                "ORDER BY idle_runs DESC, role, skill_id",
                (threshold,),
            ).fetchall()
        return [{"role": r[0], "skill_id": r[1], "idle_runs": r[2]} for r in rows]

    # ---- gaps -----------------------------------------------------------------------

    def record_gap(self, role: str, description: str, best_score: float, *, run_id: str, task_id: str) -> None:
        cleaned = " ".join(description.split())[:300]
        stamp = _now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO gaps VALUES (?, ?, ?, 1, ?, ?, ?, ?) ON CONFLICT(role, description) DO UPDATE SET "
                "count = count + 1, last_seen = excluded.last_seen, best_score = excluded.best_score",
                (role, cleaned, best_score, stamp, stamp, run_id, task_id),
            )

    def gaps(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, description, best_score, count, last_seen, run_id, task_id FROM gaps "
                "ORDER BY count DESC, last_seen DESC"
            ).fetchall()
        return [
            {
                "role": r[0],
                "description": r[1],
                "best_score": round(r[2], 3),
                "count": r[3],
                "last_seen": r[4],
                "run_id": r[5],
                "task_id": r[6],
            }
            for r in rows
        ]


@dataclass(frozen=True)
class Match:
    record: SkillRecord
    score: float
    why: str
    status: str


@dataclass
class RoleLoadout:
    role: str
    active: list[Match] = field(default_factory=list)  # approved, ranked, deduplicated, within the cap
    pending: list[Match] = field(default_factory=list)  # waiting for a person
    over_cap: list[Match] = field(default_factory=list)  # approved, but the cap was reached
    collapsed: dict[str, str] = field(default_factory=dict)  # near-duplicate: the skill it collapsed into
    excluded: dict[str, str] = field(default_factory=dict)  # skill: why it can't be used


def exclusion_reason(role: Role, record: SkillRecord, *, allow_uncategorized: bool = False) -> str | None:
    """Why this role can't use this skill at all, or None."""
    if record.trust == "quarantined":
        return "quarantined"
    if record.category == UNCATEGORIZED and not allow_uncategorized:
        return "uncategorized: a person must assign a category first"
    needed = {normalize_tool(tool) for tool in record.tools}
    forbidden = needed & set(role.forbidden_tools)
    if forbidden:
        return f"needs a forbidden tool: {', '.join(sorted(forbidden))}"
    missing = needed - set(role.allowed_tools)
    if missing:
        return f"needs a tool this role isn't allowed: {', '.join(sorted(missing))}"
    return None


def _feedback_adjustment(usage: dict[str, int]) -> float:
    """Past QA results nudge the ranking, once there's enough to go on. They can't outweigh fit."""
    judged = usage["passed"] + usage["failed"]
    if judged < FEEDBACK_MIN_USES:
        return 0.0
    return FEEDBACK_WEIGHT * (2 * usage["passed"] / judged - 1)


def _best_roles_per_skill(scored: dict[str, list[tuple[Match, Any]]]) -> set[tuple[str, str]]:
    """Which suggestions survive. A person's approval always survives, whatever the score."""
    by_skill: dict[str, list[tuple[float, str, str]]] = {}
    keep: set[tuple[str, str]] = set()
    for role, items in scored.items():
        for match, _ in items:
            if match.status == "approved":
                keep.add((role, match.record.skill_id))
            else:
                by_skill.setdefault(match.record.skill_id, []).append((match.score, role, match.record.skill_id))
    for skill_id, rows in by_skill.items():
        rows.sort(key=lambda row: (-row[0], row[1]))
        for score, role, _ in rows[:MAX_ROLES_PER_SKILL]:
            if score >= rows[0][0] - ROLE_MARGIN:
                keep.add((role, skill_id))
    return keep


def compute_loadouts(
    org: Org,
    skills: list[tuple[SkillRecord, Any]],
    embedder: Embedder,
    decisions: Decisions,
    usage_for: Callable[[str, str], dict[str, int]],
    *,
    threshold: float = MATCH_THRESHOLD,
    verdicts: dict[tuple[str, str], Any] | None = None,
) -> dict[str, RoleLoadout]:
    """Score every eligible skill against every role. Nothing here changes what any role may use.

    With verdicts from the judge (skills/rerank.py), its call on fit replaces the score threshold and
    the limit on roles per skill, and the score only ranks. A person's approval always survives.
    """
    cache: dict[str, Any] = {}

    def vector_for(text: str) -> Any:
        if text not in cache:
            cache[text] = embedder.embed_query(text)
        return cache[text]

    results: dict[str, RoleLoadout] = {}
    scored: dict[str, list[tuple[Match, Any]]] = {}
    for ref, role in sorted(org.roles.items()):
        needs = [*role.skill_needs, *role.responsibilities] or [role.mission]
        weights = np.array(
            [1.0] * len(role.skill_needs) + [RESPONSIBILITY_WEIGHT] * len(role.responsibilities) or [1.0],
            dtype=np.float32,
        )
        matrix = np.vstack([vector_for(need) for need in needs])
        loadout = RoleLoadout(ref)
        results[ref] = loadout
        scored[ref] = []
        for record, vector in skills:
            reason = exclusion_reason(role, record)
            if reason:
                loadout.excluded[record.skill_id] = reason
                continue
            status = decisions.status(ref, record)
            if status == "rejected":
                loadout.excluded[record.skill_id] = "rejected by a person"
                continue
            similarities = (matrix @ vector) * weights
            best = int(np.argmax(similarities))
            similarity = float(similarities[best])
            fits_category = record.category in role.allowed_skill_categories
            usage = usage_for(ref, record.skill_id)
            category_fit = CATEGORY_BONUS if fits_category else -CATEGORY_PENALTY
            score = similarity + category_fit + _feedback_adjustment(usage)
            verdict = verdicts.get((ref, record.skill_id)) if verdicts is not None else None
            if status != "approved":
                if verdicts is not None and not (verdict and verdict.fit):
                    continue
                if verdicts is None and score < threshold:
                    continue
            why = f'fits "{needs[best][:50]}" ({similarity:.2f})'
            if verdict is not None and verdict.fit:
                why = verdict.reason
            if fits_category:
                why += f"; category {record.category} is allowed"
            if usage["uses"]:
                why += f"; used {usage['uses']}x, {usage['passed']} passed QA"
            scored[ref].append((Match(record, score, why, status), vector))

    keep = _best_roles_per_skill(scored) if verdicts is None else None
    for ref, role in sorted(org.roles.items()):
        loadout = results[ref]
        candidates = [item for item in scored[ref] if keep is None or (ref, item[0].record.skill_id) in keep]
        # Keep the approved copy of a near-duplicate if there is one, and otherwise the better match.
        candidates.sort(key=lambda item: (item[0].status != "approved", -item[0].score, item[0].record.skill_id))
        kept: list[tuple[Match, Any]] = []
        for match, vector in candidates:
            duplicate_of = next(
                (other.record.skill_id for other, kv in kept if float(vector @ kv) >= DUPLICATE_SIMILARITY), None
            )
            if duplicate_of:
                loadout.collapsed[match.record.skill_id] = duplicate_of
            else:
                kept.append((match, vector))

        ranked = sorted((m for m, _ in kept), key=lambda m: (-m.score, m.record.skill_id))
        approved = [m for m in ranked if m.status == "approved"]
        loadout.active = approved[: role.max_skills]
        loadout.over_cap = approved[role.max_skills :]
        loadout.pending = [m for m in ranked if m.status == "pending"]
    return results
