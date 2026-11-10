"""When a company should grow: what the root role is shown before it proposes a new role or department.

A company that only ever has the roles it started with routes everything to the nearest one, and the work that fits
nobody gets done badly, or by a person by hand. The root role proposes what's missing, from evidence code gathers, and
a person decides. Nothing here changes the company.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..org import Org
from .pipeline.state import FINAL_TASK_ID, RunState

if TYPE_CHECKING:
    from .company import CompanyStore
    from .outside import OutsideStore

RECENT_COMMANDS = 10
MAX_MISSION_CHARS = 70
MAX_FEEDBACK_CHARS = 220
MAX_OUTSIDE_SHOWN = 3


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def roster(org: Org) -> list[str]:
    """Every department, what its head is for, and its roles, so a proposal is never for one that exists. Names only
    below the head: this is sent at every retrospective, and a role's id says what it does."""
    lines = []
    for department in org.departments.values():
        mission = _short(org.role(department.head_ref).mission, MAX_MISSION_CHARS)
        lines.append(f"{department.id} ({mission}): {', '.join(department.roles)}")
    return lines


def signals(org: Org, state: RunState, company: CompanyStore, outside: OutsideStore | None = None) -> list[str]:
    """What happened in this command and across recent ones that could mean a role is missing. All from records."""
    found: list[str] = []
    for task in state.tasks.values():
        if task.task_id == FINAL_TASK_ID or not task.rework_count:
            continue
        last = _short(task.feedback[-1], MAX_FEEDBACK_CHARS) if task.feedback else ""
        found.append(f"{task.task_id}, by {task.owner_role}, was sent back {task.rework_count} time(s). Last: {last}")
    counters = state.counters
    if counters.escalations:
        found.append(f"Escalations in this command: {counters.escalations}.")
    if counters.handoffs:
        found.append(f"Work handed between departments in this command: {counters.handoffs}.")
    commands, used = company.departments_used(RECENT_COMMANDS)
    root_department = org.root_role.split("/", 1)[0]
    idle = [d for d in org.departments if d != root_department and d not in used]
    if commands >= 2 and idle:
        found.append(f"Departments given no work in the last {commands} commands: {', '.join(idle)}.")
    if outside is not None:
        by_others = [o for o in outside.outcomes(limit=200) if o.role not in org.roles]
        if by_others:
            shown = ", ".join(o.ref for o in by_others[:MAX_OUTSIDE_SHOWN])
            found.append(f"Put out by someone who isn't one of its roles: {len(by_others)}, such as {shown}.")
    for proposal in company.org_proposals("pending"):
        found.append(f"Already waiting for a person: {proposal.line()}")
    return found
