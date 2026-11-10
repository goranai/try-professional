"""What the company knows about its own work, across commands: backlog, tasks, work log, handoffs, tokens.

Work state is derived, not authored. Code projects it from run state, so no model can write it directly,
and it can be rebuilt from the ledger. It's separate from memory: this is what happened, and memory is
what's worth keeping.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..org import Org
from .pipeline.state import FINAL_TASK_ID, PackageStatus, RunPhase, RunState, TaskStatus

FORMAT_VERSION = 2  # 2: organization proposals, in 0.6.4
MAX_LOG_TEXT = 240
LESSON_KINDS = ("lesson", "team_lesson")

_MIGRATIONS = [
    """
    CREATE TABLE commands (
        run_id TEXT PRIMARY KEY,
        goal TEXT NOT NULL,
        status TEXT NOT NULL,
        size TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        cached_input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        calls INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE queued (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        goal TEXT NOT NULL,
        added_at TEXT NOT NULL
    );
    CREATE TABLE tasks (
        run_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        title TEXT NOT NULL,
        owner_role TEXT NOT NULL,
        status TEXT NOT NULL,
        waiting_on TEXT NOT NULL,
        PRIMARY KEY (run_id, task_id)
    );
    CREATE INDEX tasks_owner ON tasks (owner_role, status);
    CREATE TABLE work_log (
        key TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        role TEXT NOT NULL,
        at TEXT NOT NULL,
        kind TEXT NOT NULL,
        text TEXT NOT NULL
    );
    CREATE INDEX work_log_role ON work_log (role, at);
    CREATE TABLE log_summaries (
        role TEXT NOT NULL,
        period TEXT NOT NULL,
        entries INTEGER NOT NULL,
        text TEXT NOT NULL,
        PRIMARY KEY (role, period)
    );
    CREATE TABLE handoffs (
        run_id TEXT NOT NULL,
        package_id TEXT NOT NULL,
        from_department TEXT NOT NULL,
        to_department TEXT NOT NULL,
        request TEXT NOT NULL,
        status TEXT NOT NULL,
        PRIMARY KEY (run_id, package_id)
    );
    CREATE TABLE memory_proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tier TEXT NOT NULL,
        owner TEXT NOT NULL,
        text TEXT NOT NULL,
        source_run TEXT NOT NULL,
        source TEXT NOT NULL,
        proposed_by TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        decided_at TEXT,
        decided_by TEXT,
        UNIQUE (tier, owner, text)
    );
    """,
    """
    CREATE TABLE org_proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        department TEXT NOT NULL,
        role_id TEXT NOT NULL,
        body TEXT NOT NULL,
        source_run TEXT NOT NULL,
        proposed_by TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        decided_at TEXT,
        decided_by TEXT,
        UNIQUE (department, role_id)
    );
    """,
]


class CompanyFormatError(RuntimeError):
    """The company's data was written by a newer version. Nothing was changed."""


@dataclass(frozen=True)
class WorkEntry:
    key: str
    run_id: str
    role: str
    kind: str
    text: str


@dataclass(frozen=True)
class OrgProposal:
    """A role, or a department and its head, that the root role proposed. `change` is the OrgChange as proposed."""

    id: int
    change: dict[str, Any]
    source_run: str
    proposed_by: str
    status: str

    @property
    def ref(self) -> str:
        return f"{self.change['department']}/{self.change['role_id']}"

    def line(self) -> str:
        what = "New department" if self.change["kind"] == "add_department" else "New role"
        head = ", headed by" if self.change["kind"] == "add_department" else ":"
        return (
            f"#{self.id} {what} {self.change['department']}{head} {self.change['title']} ({self.ref}). "
            f"{_capped(self.change['mission'], 160)} Because: {_capped(self.change['because'], 200)}"
        )


@dataclass(frozen=True)
class Proposal:
    id: int
    tier: str
    owner: str
    text: str
    source_run: str
    source: str
    proposed_by: str
    status: str


def _capped(text: Any, limit: int = MAX_LOG_TEXT) -> str:
    """One printable line. This text came from a model, and reaches prompts and terminals."""
    return " ".join("".join(c if c.isprintable() else " " for c in str(text)).split())[:limit]


def _command_status(state: RunState) -> str:
    if state.phase == RunPhase.DONE:
        return "done"
    if state.phase == RunPhase.FAILED:
        return "cancelled" if state.cancelled else "failed"
    if state.phase == RunPhase.BLOCKED:
        return "blocked"
    return "running"


def _task_status(status: TaskStatus, command: str) -> str:
    if status == TaskStatus.APPROVED:
        return "completed"
    if status == TaskStatus.FAILED:
        return "failed"
    if command in ("cancelled", "failed"):
        return "cancelled"
    if status in (TaskStatus.ESCALATED, TaskStatus.WAITING):
        return "blocked"
    return "open"


def _entries(state: RunState, org: Org) -> Iterable[WorkEntry]:
    """Everything a run has added to work logs so far. Keys make this safe to repeat."""
    run = state.run_id
    root = org.root_role

    def entry(name: str, role: str, kind: str, text: Any) -> WorkEntry:
        return WorkEntry(f"{run}:{name}", run, role, kind, _capped(text))

    if state.charter_artifact_id:
        yield entry("charter", root, "did", f"Took command: {state.goal}")
    if state.plan_artifact_id:
        owners = sorted({p.owner_department for p in state.packages.values()})
        verb = "Routed to" if state.size == "small" else "Planned work for"
        yield entry("plan", root, "did", f"{verb} {', '.join(owners)}")
    for package in state.packages.values():
        tasks = [t for t in state.tasks.values() if t.work_package_id == package.id]
        if tasks and package.status != PackageStatus.PENDING:
            head = org.departments[package.owner_department].head_ref
            yield entry(f"assigned:{package.id}", head, "did", f"Assigned {len(tasks)} task(s): {package.title}")
    for task in state.tasks.values():
        if task.task_id == FINAL_TASK_ID:
            continue
        for number, feedback in enumerate(task.feedback):
            yield entry(f"feedback:{task.task_id}:{number}", task.owner_role, "feedback", f"{task.title}: {feedback}")
        if task.status != TaskStatus.APPROVED:
            continue
        artifact = state.artifacts.get(task.notes_artifact_id or "")
        notes = artifact.payload if artifact else {}
        yield entry(f"did:{task.task_id}", task.owner_role, "did", notes.get("did") or task.title)
        for kind in ("decided", "promised"):
            for number, text in enumerate(notes.get(kind, [])):
                yield entry(f"{kind}:{task.task_id}:{number}", task.owner_role, kind, text)
        for kind in ("lesson", "team_lesson"):
            if notes.get(kind):
                yield entry(f"{kind}:{task.task_id}", task.owner_role, kind, notes[kind])
    if state.final_artifact_id and state.phase == RunPhase.DONE:
        yield entry("final", root, "did", f"Delivered: {state.goal}")


class CompanyStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        if version > len(_MIGRATIONS):
            self._conn.close()
            raise CompanyFormatError(
                f"{self.path} has format {version}, and this version of trypro understands up to "
                f"{len(_MIGRATIONS)}. Update trypro. Nothing was changed."
            )
        for number, script in enumerate(_MIGRATIONS[version:], start=version + 1):
            self._conn.executescript(f"BEGIN;{script}PRAGMA user_version = {number};COMMIT;")

    @property
    def format_version(self) -> int:
        with self._lock:
            return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- projection -------------------------------------------------------------

    def sync(self, state: RunState, org: Org, at: str) -> list[WorkEntry]:
        """Bring work state up to date with one run. Returns only entries it hadn't seen."""
        command = _command_status(state)
        new: list[WorkEntry] = []
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO commands (run_id, goal, status, size, created_at, updated_at, input_tokens, "
                "cached_input_tokens, output_tokens, calls) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET status=excluded.status, size=excluded.size, "
                "updated_at=excluded.updated_at, input_tokens=excluded.input_tokens, "
                "cached_input_tokens=excluded.cached_input_tokens, output_tokens=excluded.output_tokens, "
                "calls=excluded.calls",
                (
                    state.run_id,
                    state.goal,
                    command,
                    state.size,
                    at,
                    at,
                    state.usage.input_tokens,
                    state.usage.cached_input_tokens,
                    state.usage.output_tokens,
                    state.usage.calls,
                ),
            )
            self._conn.execute("DELETE FROM tasks WHERE run_id = ?", (state.run_id,))
            for task in state.tasks.values():
                if task.task_id == FINAL_TASK_ID:
                    continue
                waiting = [
                    state.tasks[d].owner_role
                    for d in task.depends_on
                    if d in state.tasks and state.tasks[d].status != TaskStatus.APPROVED
                ]
                self._conn.execute(
                    "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        state.run_id,
                        task.task_id,
                        _capped(task.title, 160),
                        task.owner_role,
                        _task_status(task.status, command),
                        json.dumps(sorted(set(waiting))),
                    ),
                )
            self._sync_handoffs(state, command)
            for item in _entries(state, org):
                cursor = self._conn.execute(
                    "INSERT OR IGNORE INTO work_log VALUES (?, ?, ?, ?, ?, ?)",
                    (item.key, item.run_id, item.role, at, item.kind, item.text),
                )
                if cursor.rowcount:
                    new.append(item)
        return new

    def _sync_handoffs(self, state: RunState, command: str) -> None:
        requests = [a for a in state.artifacts.values() if a.type == "Handoff"]
        for package in state.packages.values():
            if package.origin != "handoff":
                continue
            source = next((a for a in requests if a.payload.get("request") == package.title), None)
            if package.status in (PackageStatus.APPROVED, PackageStatus.FAILED):
                status = package.status.value
            else:
                status = "cancelled" if command in ("cancelled", "failed") else "open"
            self._conn.execute(
                "INSERT OR REPLACE INTO handoffs VALUES (?, ?, ?, ?, ?, ?)",
                (
                    state.run_id,
                    package.id,
                    str(source.payload.get("from_department", "")) if source else "",
                    package.owner_department,
                    _capped(package.title, 160),
                    status,
                ),
            )

    def rebuild(self, states: Iterable[RunState], org: Org, at: str) -> None:
        """Work state is derived, so losing it costs nothing. Proposals and the backlog aren't derived, and stay."""
        with self._lock, self._conn:
            for table in ("commands", "tasks", "work_log", "handoffs"):
                self._conn.execute(f"DELETE FROM {table}")
        for state in states:
            self.sync(state, org, at)

    # ---- what one role sees -----------------------------------------------------

    def queue(self, role: str, *, completed: int = 2) -> dict[str, list[dict[str, Any]]]:
        """A role's tasks, with the command each one came from."""
        result: dict[str, list[dict[str, Any]]] = {"open": [], "blocked": [], "completed": []}
        query = (
            "SELECT t.task_id, t.title, t.status, t.waiting_on, c.goal FROM tasks t "
            "JOIN commands c ON c.run_id = t.run_id WHERE t.owner_role = ? AND t.status = ? "
            "ORDER BY c.created_at DESC, t.task_id LIMIT ?"
        )
        with self._lock:
            for status, limit in (("open", 5), ("blocked", 5), ("completed", completed)):
                for row in self._conn.execute(query, (role, status, limit)).fetchall():
                    result[status].append(
                        {
                            "task_id": row[0],
                            "title": row[1],
                            "status": row[2],
                            "waiting_on": json.loads(row[3]),
                            "origin": row[4],
                        }
                    )
        return result

    def recent_log(self, role: str, limit: int = 5) -> list[dict[str, str]]:
        """Newest first. Older work appears as its monthly summary once it has been compacted."""
        placeholders = ",".join("?" for _ in LESSON_KINDS)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT at, kind, text FROM work_log WHERE role = ? AND kind NOT IN ({placeholders}) "
                "ORDER BY at DESC, rowid DESC LIMIT ?",
                (role, *LESSON_KINDS, limit),
            ).fetchall()
            room = limit - len(rows)
            summaries = self._conn.execute(
                "SELECT period, text FROM log_summaries WHERE role = ? ORDER BY period DESC LIMIT ?",
                (role, max(room, 0)),
            ).fetchall()
        log = [{"at": r[0], "kind": r[1], "text": r[2]} for r in rows]
        return [*log, *[{"at": r[0], "kind": "summary", "text": r[1]} for r in summaries]]

    def handoffs_for(self, department: str) -> dict[str, list[str]]:
        with self._lock:
            waiting = self._conn.execute(
                "SELECT to_department, request FROM handoffs WHERE from_department = ? AND status = 'open'",
                (department,),
            ).fetchall()
            owed = self._conn.execute(
                "SELECT from_department, request FROM handoffs WHERE to_department = ? AND status = 'open'",
                (department,),
            ).fetchall()
        return {
            "waiting_on": [f"{r[0]}: {r[1]}" for r in waiting],
            "others_wait_on_me": [f"{r[0] or 'another department'}: {r[1]}" for r in owed],
        }

    def open_task_counts(self, roles: Iterable[str]) -> dict[str, int]:
        counts = dict.fromkeys(roles, 0)
        with self._lock:
            for role in counts:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE owner_role = ? AND status IN ('open', 'blocked')", (role,)
                ).fetchone()
                counts[role] = int(row[0])
        return counts

    def team_status(self, roles: Iterable[str]) -> list[dict[str, Any]]:
        """A manager's view of each direct report."""
        status = []
        with self._lock:
            for role in roles:
                row = self._conn.execute(
                    "SELECT SUM(status = 'open'), SUM(status = 'blocked') FROM tasks WHERE owner_role = ?", (role,)
                ).fetchone()
                last = self._conn.execute(
                    "SELECT text FROM work_log WHERE role = ? AND kind = 'did' ORDER BY at DESC, rowid DESC LIMIT 1",
                    (role,),
                ).fetchone()
                status.append(
                    {
                        "role": role,
                        "open": int(row[0] or 0),
                        "blocked": int(row[1] or 0),
                        "last": last[0] if last else "",
                    }
                )
        return status

    # ---- backlog ----------------------------------------------------------------

    def add_queued(self, goal: str, at: str) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute("INSERT INTO queued (goal, added_at) VALUES (?, ?)", (goal, at))
            return int(cursor.lastrowid or 0)

    def queued(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT id, goal, added_at FROM queued ORDER BY id").fetchall()
        return [{"id": r[0], "goal": r[1], "added_at": r[2]} for r in rows]

    def take_queued(self, queue_id: int | None = None) -> dict[str, Any] | None:
        """Remove a queued command, oldest first unless one is named."""
        with self._lock, self._conn:
            if queue_id is None:
                row = self._conn.execute("SELECT id, goal FROM queued ORDER BY id LIMIT 1").fetchone()
            else:
                row = self._conn.execute("SELECT id, goal FROM queued WHERE id = ?", (queue_id,)).fetchone()
            if row is None:
                return None
            self._conn.execute("DELETE FROM queued WHERE id = ?", (row[0],))
        return {"id": row[0], "goal": row[1]}

    def unfinished(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id, goal, status, updated_at FROM commands WHERE status IN ('running', 'blocked') "
                "ORDER BY created_at"
            ).fetchall()
        return [{"run_id": r[0], "goal": r[1], "status": r[2], "updated_at": r[3]} for r in rows]

    def open_handoffs(self) -> list[dict[str, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id, from_department, to_department, request FROM handoffs WHERE status = 'open'"
            ).fetchall()
        return [{"run_id": r[0], "from": r[1], "to": r[2], "request": r[3]} for r in rows]

    # ---- cost -------------------------------------------------------------------

    def tokens_by_month(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT substr(created_at, 1, 7), COUNT(*), SUM(input_tokens), SUM(cached_input_tokens), "
                "SUM(output_tokens), SUM(calls) FROM commands GROUP BY 1 ORDER BY 1 DESC"
            ).fetchall()
        return [
            {"month": r[0], "commands": r[1], "input": r[2], "cached_input": r[3], "output": r[4], "calls": r[5]}
            for r in rows
        ]

    def tokens_by_command(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id, goal, size, status, input_tokens, cached_input_tokens, output_tokens, calls "
                "FROM commands ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "run_id": r[0],
                "goal": r[1],
                "size": r[2],
                "status": r[3],
                "input": r[4],
                "cached_input": r[5],
                "output": r[6],
                "calls": r[7],
            }
            for r in rows
        ]

    # ---- memory proposals -------------------------------------------------------

    def propose_memory(
        self, tier: str, owner: str, text: str, *, source_run: str, source: str, proposed_by: str, at: str
    ) -> None:
        """Repeating a pending or decided proposal does nothing, so a person isn't asked twice."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO memory_proposals (tier, owner, text, source_run, source, proposed_by, status, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
                (tier, owner, text, source_run, source, proposed_by, at),
            )

    def pending_memory(self, tier: str, owner: str | None = None, limit: int = 50) -> list[Proposal]:
        query = (
            "SELECT id, tier, owner, text, source_run, source, proposed_by, status FROM memory_proposals "
            "WHERE status = 'pending' AND tier = ?"
        )
        args: list[Any] = [tier]
        if owner is not None:
            query += " AND owner = ?"
            args.append(owner)
        with self._lock:
            rows = self._conn.execute(f"{query} ORDER BY id LIMIT ?", (*args, limit)).fetchall()
        return [Proposal(*row) for row in rows]

    def decide_memory(self, proposal_id: int, accepted: bool, *, by: str, at: str) -> Proposal | None:
        """Compare-and-swap on 'pending', so a proposal can't be applied twice."""
        status = "accepted" if accepted else "rejected"
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE memory_proposals SET status = ?, decided_at = ?, decided_by = ? "
                "WHERE id = ? AND status = 'pending'",
                (status, at, by, proposal_id),
            )
            if not cursor.rowcount:
                return None
            row = self._conn.execute(
                "SELECT id, tier, owner, text, source_run, source, proposed_by, status FROM memory_proposals "
                "WHERE id = ?",
                (proposal_id,),
            ).fetchone()
        return Proposal(*row)

    # ---- organization proposals -------------------------------------------------

    def propose_org_change(self, change: dict[str, Any], *, source_run: str, proposed_by: str, at: str) -> bool:
        """Repeating a role that's pending or was decided does nothing, so a person isn't asked twice."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO org_proposals (kind, department, role_id, body, source_run, proposed_by, "
                "status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
                (change["kind"], change["department"], change["role_id"], json.dumps(change, ensure_ascii=False),
                 source_run, proposed_by, at),
            )  # fmt: skip
            return bool(cursor.rowcount)

    def org_proposals(self, status: str | None = "pending", source_run: str | None = None) -> list[OrgProposal]:
        query = "SELECT id, body, source_run, proposed_by, status FROM org_proposals WHERE 1 = 1"
        args: list[Any] = []
        if status:
            query += " AND status = ?"
            args.append(status)
        if source_run:
            query += " AND source_run = ?"
            args.append(source_run)
        with self._lock:
            rows = self._conn.execute(f"{query} ORDER BY id", args).fetchall()
        return [OrgProposal(r[0], json.loads(r[1]), r[2], r[3], r[4]) for r in rows]

    def decide_org_change(self, proposal_id: int, status: str, *, by: str, at: str) -> OrgProposal | None:
        """Compare-and-swap on 'pending', so a change can't be applied twice. `status` is accepted or rejected."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE org_proposals SET status = ?, decided_at = ?, decided_by = ? "
                "WHERE id = ? AND status = 'pending'",
                (status, at, by, proposal_id),
            )
            if not cursor.rowcount:
                return None
            row = self._conn.execute(
                "SELECT id, body, source_run, proposed_by, status FROM org_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
        return OrgProposal(row[0], json.loads(row[1]), row[2], row[3], row[4])

    def reopen_org_change(self, proposal_id: int) -> None:
        """An accepted change that couldn't be applied goes back to waiting, so it's never lost."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE org_proposals SET status = 'pending', decided_at = NULL, decided_by = NULL WHERE id = ?",
                (proposal_id,),
            )

    def departments_used(self, last_commands: int) -> tuple[int, set[str]]:
        """How many of the latest finished or stopped commands there were, and which departments did work in them."""
        with self._lock:
            runs = [r[0] for r in self._conn.execute(
                "SELECT run_id FROM commands WHERE status IN ('done', 'failed', 'cancelled') "
                "ORDER BY created_at DESC LIMIT ?", (last_commands,)).fetchall()]  # fmt: skip
            if not runs:
                return 0, set()
            marks = ",".join("?" * len(runs))
            owners = self._conn.execute(f"SELECT DISTINCT owner_role FROM tasks WHERE run_id IN ({marks})", runs)
            return len(runs), {row[0].split("/", 1)[0] for row in owners.fetchall()}

    # ---- compaction -------------------------------------------------------------

    def compact(self, before: str, *, keep_per_role: int = 20) -> int:
        """Replace old work log entries with monthly summaries, using no model. Returns how many went.

        Each role keeps its newest entries whatever their age, so a brief never runs out of history.
        Raw records stay in the archived audit logs.
        """
        removed = 0
        with self._lock, self._conn:
            roles = [r[0] for r in self._conn.execute("SELECT DISTINCT role FROM work_log").fetchall()]
            for role in roles:
                rows = self._conn.execute(
                    "SELECT key, at, kind, text FROM work_log WHERE role = ? AND at < ? "
                    "AND key NOT IN (SELECT key FROM work_log WHERE role = ? ORDER BY at DESC, rowid DESC LIMIT ?) "
                    "ORDER BY at, rowid",
                    (role, before, role, keep_per_role),
                ).fetchall()
                periods: dict[str, list[str]] = {}
                for _, at, kind, text in rows:
                    if kind == "did":
                        periods.setdefault(at[:7], []).append(text)
                    else:
                        periods.setdefault(at[:7], [])
                for period, done in periods.items():
                    count = sum(1 for row in rows if row[1][:7] == period)
                    existing = self._conn.execute(
                        "SELECT entries, text FROM log_summaries WHERE role = ? AND period = ?", (role, period)
                    ).fetchone()
                    highlights = "; ".join(item[:60] for item in done[:3])
                    previous = int(existing[0]) if existing else 0
                    summary = f"{period}: {previous + count} log entries. Including: {highlights or 'nothing notable'}"
                    self._conn.execute(
                        "INSERT OR REPLACE INTO log_summaries VALUES (?, ?, ?, ?)",
                        (role, period, previous + count, _capped(summary)),
                    )
                for row in rows:
                    self._conn.execute("DELETE FROM work_log WHERE key = ?", (row[0],))
                removed += len(rows)
        return removed
