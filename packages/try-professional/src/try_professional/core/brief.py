"""The wake-up brief: a short stand-up every agent gets when it's activated.

Code builds it, so it costs no model call. The token budget is a hard limit, because a brief is sent on
every activation of every agent, and it's the part of the hierarchy that could quietly grow the bill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..org import Org
from .company import CompanyStore
from .pipeline.state import Purpose

STAFF_TOKENS = 300
MANAGER_TOKENS = 600
SMALL_TOKENS = 150
REVIEW_TOKENS = 100
MAX_LINE_CHARS = 160
MAX_TEAM_NAMES = 8
MAX_RESPONSIBILITIES = 2


def estimate_tokens(text: str) -> int:
    """A cautious count. Non-ASCII text, such as Indonesian names or Chinese, costs more per character."""
    plain = sum(1 for c in text if ord(c) < 128)
    return plain // 4 + (len(text) - plain) + 1


def budget_for(org: Org, role: str, purpose: Purpose, size: str) -> int:
    if purpose == Purpose.REVIEW:
        return REVIEW_TOKENS
    if size == "small":
        return SMALL_TOKENS
    return MANAGER_TOKENS if org.is_senior(role) else STAFF_TOKENS


def role_card(org: Org, ref: str) -> str:
    """Who I am, my manager, my team, my responsibilities. All of it comes from the role file."""
    role = org.role(ref)
    department = org.department_of(ref)
    manager = "the person giving commands" if role.escalation == "human" else role.escalation
    parts = [f"{role.display_name}, {department.name or department.id}. Manager: {manager}."]
    reports = org.direct_reports(ref)
    if reports:
        parts.append(f"Team: {', '.join(org.role(r).display_name for r in reports[:MAX_TEAM_NAMES])}.")
    parts.append("You do: " + "; ".join(role.responsibilities[:MAX_RESPONSIBILITIES]) + ".")
    return " ".join(parts)


@dataclass(frozen=True)
class Brief:
    card: str
    state: str
    tokens: int
    budget: int


@dataclass(frozen=True)
class Briefing:
    """What an adapter adds to an assignment."""

    brief: Brief | None = None
    # Lessons from staff, waiting for a head to decide. Only shown when that head is dispatching.
    pending_lessons: list[dict[str, Any]] = field(default_factory=list)
    # role -> open task count and skill names, so a manager can assign by fit and workload.
    team: dict[str, dict[str, Any]] = field(default_factory=dict)
    # What happened to the company's earlier work outside: a count, and the newest items.
    outside: list[str] = field(default_factory=list)
    # This run's commands waiting for a person's approval. They count as done for this run, if they're right.
    held: list[str] = field(default_factory=list)
    # At a retrospective: every role and what it's for, and the record of what didn't fit them. See org_growth.
    roster: list[str] = field(default_factory=list)
    org_record: list[str] = field(default_factory=list)


def _line(text: str) -> str:
    return text if len(text) <= MAX_LINE_CHARS else text[: MAX_LINE_CHARS - 1] + "…"


def _sections(
    org: Org, store: CompanyStore, ref: str, purpose: Purpose, size: str, personal: list[str]
) -> list[list[str]]:
    """In priority order: if the budget runs out, what's left off matters least."""
    queue = store.queue(ref)
    department = ref.split("/", 1)[0]
    handoffs = store.handoffs_for(department) if org.is_head(ref) else {"waiting_on": [], "others_wait_on_me": []}
    sections: list[list[str]] = []
    if queue["open"] or queue["blocked"]:
        work = [f"- {t['title']} (open, for: {t['origin']})" for t in queue["open"]]
        work += [f"- {t['title']} (blocked, for: {t['origin']})" for t in queue["blocked"]]
        waits = sorted({who for t in queue["open"] for who in t["waiting_on"]})
        if waits:
            work.append(f"- Waiting on: {', '.join(waits)}")
        sections.append(["Your open tasks:", *work])
    if handoffs["waiting_on"]:
        sections.append(["You're waiting on:", *[f"- {item}" for item in handoffs["waiting_on"]]])
    if handoffs["others_wait_on_me"]:
        sections.append(["Others are waiting on you:", *[f"- {item}" for item in handoffs["others_wait_on_me"]]])
    if purpose == Purpose.REVIEW:
        return sections
    log = store.recent_log(ref, limit=3 if size == "small" else 5)
    if log:
        sections.append(["What you did recently:", *[f"- {e['at'][:10]} {e['kind']}: {e['text']}" for e in log]])
    if personal:
        sections.append(["Your notes that may apply:", *[f"- {item}" for item in personal]])
    reports = org.direct_reports(ref)
    if reports and size != "small":
        team = []
        for member in store.team_status(reports):
            last = f", last: {member['last']}" if member["last"] else ""
            name = org.role(member["role"]).display_name
            team.append(f"- {name}: {member['open']} open, {member['blocked']} blocked{last}")
        sections.append(["Your team now:", *team])
    return sections


def build_brief(
    org: Org,
    store: CompanyStore,
    ref: str,
    purpose: Purpose,
    *,
    size: str = "large",
    personal: list[str] | None = None,
) -> Brief:
    budget = budget_for(org, ref, purpose, size)
    card = role_card(org, ref)
    used = estimate_tokens(card)
    kept: list[str] = []
    for section in _sections(org, store, ref, purpose, size, personal or []):
        header, *items = section
        # A heading with nothing under it is only noise, so it's added with its first item.
        pending = [_line(header)]
        for item in items:
            line = _line(item)
            cost = estimate_tokens("\n".join([*pending, line]))
            if used + cost > budget:
                break
            kept += [*pending, line]
            used += cost
            pending = []
    return Brief(card=card, state="\n".join(kept), tokens=used, budget=budget)
