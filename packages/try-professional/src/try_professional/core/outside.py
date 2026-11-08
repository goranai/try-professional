"""What a company did outside, what came of it, and what waits for a person.

Work that leaves the company, such as an issue, a pull request, a post, or an email, meets the world later. What
happened to it is the best teacher a role has, so it's recorded here, checked, and turned into lessons for the roles
that worked on it. Commands that change something outside, from a run with nobody there to ask, wait here for a
person. And a company can work on a timer: schedules live here too.

This is the company's record, written by code. No model writes to it directly: a role can only ask for something to
be tracked, and a person or a check decides what happened to it.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

OUTCOME_STATUSES = ("open", "accepted", "rejected", "duplicate", "ignored")
MAX_TEXT = 300
RECENT_SHOWN = 8
_URL = re.compile(r"https?://[^\s\"'<>)\]]+")
_EVERY = re.compile(r"^\s*(\d+)\s*([mhdw])\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
MIN_EVERY_SECONDS = 15 * 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    role TEXT NOT NULL,
    credited TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    checked_at TEXT,
    seen_replies INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS held (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    role TEXT NOT NULL,
    command TEXT NOT NULL,
    workspace TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    decided_by TEXT,
    decided_at TEXT,
    output TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS schedules (
    name TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    every_seconds INTEGER NOT NULL,
    next_at TEXT NOT NULL,
    last_at TEXT,
    last_run_id TEXT
);
"""


def _clean(text: Any, limit: int = MAX_TEXT) -> str:
    """One printable line. It may have come from a model or a stranger, and reaches prompts and terminals."""
    return " ".join("".join(c if c.isprintable() else " " for c in str(text)).split())[:limit]


def urls_in(text: str) -> list[str]:
    """Links a command printed, such as the issue `gh issue create` just opened. Order kept, repeats dropped."""
    seen: dict[str, None] = {}
    for match in _URL.findall(text or ""):
        seen.setdefault(match.rstrip(".,;:"), None)
    return list(seen)


_TITLE = re.compile(r"""(?:--title|-t)(?:\s+|=)(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|(\S+))""")


def describe_link(url: str, command: str = "") -> tuple[str, str]:
    """What kind of thing a link is, and its title if the command that made it gave one."""
    kind = "link"
    if re.search(r"github\.com/[^/]+/[^/]+/issues/\d+", url):
        kind = "issue"
    elif re.search(r"github\.com/[^/]+/[^/]+/pull/\d+", url):
        kind = "pull request"
    elif "/discussions/" in url:
        kind = "discussion"
    match = _TITLE.search(command)
    title = next((group for group in match.groups() if group), "") if match else ""
    return kind, _clean(title)


_LESSONS = {
    "accepted": "It was accepted. What led to it worked: keep doing it that way.",
    "rejected": (
        "It was turned down. Before the next one, make sure it's wanted: the project's own rules, what it already "
        "plans, and whether it's worth the reader's time."
    ),
    "duplicate": (
        "It was closed as a duplicate. Before posting, search what's already there, open and closed, and link to it "
        "if it's close."
    ),
    "ignored": "Nobody answered. A place that doesn't answer isn't worth more of the same work.",
    "open": "It's open again.",
}


def outcome_lesson(item: Outcome) -> str:
    """A lesson from what happened, written by code from the record. A quoted reply is someone else's words."""
    title = f" '{item.title}'" if item.title else ""
    note = f" What was said: {item.note}" if item.note else ""
    return _clean(f"What happened after: the {item.kind}{title} at {item.url}. {_LESSONS[item.status]}{note}", 600)


def parse_every(text: str) -> int:
    """'30m', '6h', '1d', '1w' to seconds. Raises ValueError, with what's allowed."""
    match = _EVERY.match(text or "")
    if not match:
        raise ValueError(f"'{text}' isn't an interval. Use a number and m, h, d, or w, such as 6h or 1d.")
    seconds = int(match.group(1)) * _UNIT_SECONDS[match.group(2).lower()]
    if seconds < MIN_EVERY_SECONDS:
        raise ValueError("The shortest interval is 15m.")
    return seconds


def describe_every(seconds: int) -> str:
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


@dataclass(frozen=True)
class Outcome:
    id: int
    url: str
    run_id: str
    role: str
    credited: tuple[str, ...]
    kind: str
    title: str
    created_at: str
    status: str
    note: str
    checked_at: str | None
    seen_replies: int

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "credited": list(self.credited)}


@dataclass(frozen=True)
class Held:
    id: int
    run_id: str
    role: str
    command: str
    workspace: str
    created_at: str
    status: str
    decided_by: str | None
    output: str


@dataclass(frozen=True)
class Schedule:
    name: str
    goal: str
    every_seconds: int
    next_at: str
    last_at: str | None
    last_run_id: str | None


_OUTCOME_COLUMNS = "id, url, run_id, role, credited, kind, title, created_at, status, note, checked_at, seen_replies"
_HELD_COLUMNS = "id, run_id, role, command, workspace, created_at, status, decided_by, output"


def _outcome(row: Any) -> Outcome:
    values = list(row)
    values[4] = tuple(json.loads(values[4]))
    return Outcome(*values)


class OutsideStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- outcomes ---------------------------------------------------------------

    def track(
        self, url: str, *, run_id: str, role: str, credited: list[str], kind: str, title: str, at: str
    ) -> Outcome | None:
        """Start tracking something the company put out. The same link twice is tracked once."""
        url = _clean(url, 500)
        if not _URL.fullmatch(url):
            return None
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO outcomes (url, run_id, role, credited, kind, title, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'open')",
                (url, run_id, role, json.dumps(sorted(set(credited))), _clean(kind, 40) or "link", _clean(title), at),
            )
            row = self._conn.execute(f"SELECT {_OUTCOME_COLUMNS} FROM outcomes WHERE url = ?", (url,)).fetchone()
        return _outcome(row)

    def outcome(self, outcome_id: int) -> Outcome | None:
        with self._lock:
            row = self._conn.execute(f"SELECT {_OUTCOME_COLUMNS} FROM outcomes WHERE id = ?", (outcome_id,)).fetchone()
        return _outcome(row) if row else None

    def outcomes(self, status: str | None = None, limit: int = 200) -> list[Outcome]:
        query = f"SELECT {_OUTCOME_COLUMNS} FROM outcomes"
        args: list[Any] = []
        if status:
            query += " WHERE status = ?"
            args.append(status)
        with self._lock:
            rows = self._conn.execute(f"{query} ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [_outcome(row) for row in rows]

    def settle(self, outcome_id: int, status: str, note: str, at: str) -> Outcome | None:
        """Record what happened. Returns the outcome only if its status changed, so a lesson is written once."""
        if status not in OUTCOME_STATUSES:
            raise ValueError(f"status must be one of {', '.join(OUTCOME_STATUSES)}")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE outcomes SET status = ?, note = ?, checked_at = ? WHERE id = ? AND status != ?",
                (status, _clean(note), at, outcome_id, status),
            )
            if not cursor.rowcount:
                self._conn.execute("UPDATE outcomes SET checked_at = ? WHERE id = ?", (at, outcome_id))
                return None
        return self.outcome(outcome_id)

    def saw_replies(self, outcome_id: int, count: int, at: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE outcomes SET seen_replies = ?, checked_at = ? WHERE id = ?", (count, at, outcome_id)
            )

    def record_lines(self, limit: int = RECENT_SHOWN) -> list[str]:
        """What a role is told about the company's track record: a count, and the newest items."""
        with self._lock:
            counts = dict(self._conn.execute("SELECT status, COUNT(*) FROM outcomes GROUP BY status").fetchall())
        if not counts:
            return []
        total = sum(counts.values())
        summary = ", ".join(f"{counts[s]} {s}" for s in OUTCOME_STATUSES if counts.get(s))
        lines = [f"{total} tracked: {summary}."]
        for item in self.outcomes(limit=limit):
            note = f" ({item.note})" if item.note else ""
            lines.append(f"{item.status}: {item.kind} {item.url} '{item.title}'{note}")
        return lines

    # ---- commands held for a person ---------------------------------------------

    def hold(self, *, run_id: str, role: str, command: str, workspace: Path, at: str) -> int:
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT id FROM held WHERE run_id = ? AND command = ? AND status = 'waiting'", (run_id, command)
            ).fetchone()
            if existing:
                return int(existing[0])
            cursor = self._conn.execute(
                "INSERT INTO held (run_id, role, command, workspace, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, 'waiting')",
                (run_id, role, command, str(workspace), at),
            )
            return int(cursor.lastrowid or 0)

    def held(self, status: str | None = "waiting") -> list[Held]:
        query = f"SELECT {_HELD_COLUMNS} FROM held"
        args: list[Any] = []
        if status:
            query += " WHERE status = ?"
            args.append(status)
        with self._lock:
            rows = self._conn.execute(f"{query} ORDER BY id", args).fetchall()
        return [Held(*row) for row in rows]

    def held_item(self, held_id: int) -> Held | None:
        with self._lock:
            row = self._conn.execute(f"SELECT {_HELD_COLUMNS} FROM held WHERE id = ?", (held_id,)).fetchone()
        return Held(*row) if row else None

    def decide_held(self, held_id: int, status: str, *, by: str, at: str, output: str = "") -> bool:
        """Compare-and-swap on 'waiting', so a command can't run twice."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE held SET status = ?, decided_by = ?, decided_at = ?, output = ? "
                "WHERE id = ? AND status = 'waiting'",
                (status, by, at, output[-4000:], held_id),
            )
            return bool(cursor.rowcount)

    def finish_held(self, held_id: int, output: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE held SET output = ? WHERE id = ?", (output[-4000:], held_id))

    # ---- schedules --------------------------------------------------------------

    def add_schedule(self, name: str, goal: str, every_seconds: int, now: str) -> Schedule:
        name = _clean(name, 60)
        if not re.fullmatch(r"[\w.-]+", name):
            raise ValueError("A schedule's name is one word: letters, numbers, dot, dash, or underscore.")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO schedules (name, goal, every_seconds, next_at) VALUES (?, ?, ?, ?)",
                (name, goal, every_seconds, now),
            )
        return self.schedules(name)[0]

    def schedules(self, name: str | None = None) -> list[Schedule]:
        query = "SELECT name, goal, every_seconds, next_at, last_at, last_run_id FROM schedules"
        with self._lock:
            if name is None:
                rows = self._conn.execute(f"{query} ORDER BY name").fetchall()
            else:
                rows = self._conn.execute(f"{query} WHERE name = ?", (name,)).fetchall()
        return [Schedule(*row) for row in rows]

    def remove_schedule(self, name: str) -> bool:
        with self._lock, self._conn:
            return bool(self._conn.execute("DELETE FROM schedules WHERE name = ?", (name,)).rowcount)

    def due(self, now: str) -> list[Schedule]:
        return [s for s in self.schedules() if s.next_at <= now]

    def ran(self, name: str, now: str, run_id: str) -> None:
        """Next time is counted from now, so a machine that was off runs a missed schedule once, not many times."""
        schedule = self.schedules(name)
        if not schedule:
            return
        later = datetime.fromisoformat(now) + timedelta(seconds=schedule[0].every_seconds)
        upcoming = later.isoformat(timespec="milliseconds")
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE schedules SET last_at = ?, last_run_id = ?, next_at = ? WHERE name = ?",
                (now, run_id, upcoming, name),
            )
