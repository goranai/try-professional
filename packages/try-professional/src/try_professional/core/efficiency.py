"""How efficiently a command ran: time, model calls, tokens, and where they went.

A company of agents is worth it only if it saves time, so this is measured for every run, from the audit log alone.
That means it works on any run, by any version, and nothing a model said goes into it. Time waiting for a person is
counted apart, because it isn't the company's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class Efficiency:
    run_id: str
    status: str = ""
    seconds: float = 0.0
    waiting_seconds: float = 0.0
    calls: int = 0
    tokens: int = 0
    steps: dict[str, int] = field(default_factory=dict)  # purpose -> how many times a role was asked
    calls_by_purpose: dict[str, int] = field(default_factory=dict)
    tokens_by_purpose: dict[str, int] = field(default_factory=dict)
    calls_by_role: dict[str, int] = field(default_factory=dict)
    roles: int = 0
    handoffs: list[str] = field(default_factory=list)
    rework: int = 0
    refused: int = 0
    escalations: int = 0
    failed: int = 0
    tool_calls: int = 0

    @property
    def working_seconds(self) -> float:
        return max(0.0, self.seconds - self.waiting_seconds)

    @property
    def review_share(self) -> float:
        return self.tokens_by_purpose.get("review", 0) / self.tokens if self.tokens else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "working_seconds": self.working_seconds, "review_share": round(self.review_share, 3)}

    def lines(self) -> list[str]:
        minutes = self.working_seconds / 60
        waited = f", plus {self.waiting_seconds / 60:.1f} waiting for a person" if self.waiting_seconds >= 1 else ""
        out = [
            f"Took {minutes:.1f} min of work{waited}: {self.calls} model calls, {self.tokens:,} tokens, "
            f"{self.roles} roles, {self.tool_calls} tool calls.",
            "Where it went: "
            + ", ".join(
                f"{purpose} {self.calls_by_purpose.get(purpose, 0)} calls"
                for purpose in sorted(self.steps, key=lambda p: -self.calls_by_purpose.get(p, 0))
            )
            + ".",
        ]
        waste = []
        if self.rework:
            waste.append(f"{self.rework} sent back for rework")
        if self.refused:
            waste.append(f"{self.refused} hand-ins refused")
        if self.failed:
            waste.append(f"{self.failed} steps failed")
        if self.escalations:
            waste.append(f"{self.escalations} escalations")
        if self.handoffs:
            waste.append(f"{len(self.handoffs)} handoffs ({'; '.join(self.handoffs)})")
        out.append("Extra rounds: " + (", ".join(waste) if waste else "none") + ".")
        return out


def _when(record: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(record["ts"]))


def measure(audit_path: Path, run_id: str = "") -> Efficiency:
    """Everything here comes from the run's own audit log."""
    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    report = Efficiency(run_id=run_id or audit_path.parent.name)
    if not records:
        return report
    report.seconds = (_when(records[-1]) - _when(records[0])).total_seconds()
    purpose_of: dict[str, str] = {}
    blocked_at: datetime | None = None
    roles: set[str] = set()
    for record in records:
        kind, data = record.get("kind"), record.get("data") or {}
        inner = data.get("kind")
        if kind == "action" and inner == "invoke_role":
            purpose = str(data.get("purpose", ""))
            purpose_of[str(data.get("invocation_id", ""))] = purpose
            report.steps[purpose] = report.steps.get(purpose, 0) + 1
            roles.add(str(data.get("role", "")))
        elif kind == "event" and inner == "usage_recorded":
            purpose = purpose_of.get(str(data.get("invocation_id", "")), "other")
            tokens = int(data.get("input_tokens") or 0) + int(data.get("output_tokens") or 0)
            report.calls += 1
            report.tokens += tokens
            report.calls_by_purpose[purpose] = report.calls_by_purpose.get(purpose, 0) + 1
            report.tokens_by_purpose[purpose] = report.tokens_by_purpose.get(purpose, 0) + tokens
            role = str(data.get("role") or "")
            if role:
                report.calls_by_role[role] = report.calls_by_role.get(role, 0) + 1
        elif kind == "event" and inner == "handoff_raised":
            payload = data.get("payload") or {}
            report.handoffs.append(f"{payload.get('from_department')} to {payload.get('to_department')}")
        elif kind == "event" and inner == "escalation_raised":
            report.escalations += 1
        elif kind == "event" and inner == "invocation_failed":
            report.failed += 1
        elif kind == "event" and inner == "artifact_submitted":
            if (data.get("payload") or {}).get("decision") == "changes_requested":
                report.rework += 1
        elif kind == "invalid_output" or kind == "rejected":
            report.refused += 1
        elif kind == "tool_call":
            report.tool_calls += 1
        elif kind == "phase":
            if data.get("to") == "blocked":
                blocked_at = _when(record)
            elif data.get("from") == "blocked" and blocked_at is not None:
                report.waiting_seconds += (_when(record) - blocked_at).total_seconds()
                blocked_at = None
            if data.get("to") in ("done", "failed", "blocked"):
                report.status = str(data.get("to"))
    if blocked_at is not None:
        # Still waiting: the time after it stopped isn't the company's work.
        report.seconds = (blocked_at - _when(records[0])).total_seconds()
    # A step that failed, such as a model error, is escalated to a manager by the pipeline, not by a role.
    report.escalations = max(report.escalations, report.steps.get("escalation", 0))
    report.roles = len(roles - {""})
    return report
