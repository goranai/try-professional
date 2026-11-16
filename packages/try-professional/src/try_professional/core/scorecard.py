"""Role cost and contribution, from audit records across commands.

The 0.7.4 feature brief, supplied on 2026-11-15, reports review and rework at about 29% of lab cost,
and administration using about eight of fifteen roles in a contribution farm.
Those observations ask for evidence before changing the roster, not automatic cuts.
Acceptance means the pipeline accepted a hand-in, not that its claims are true.
Repeating means an identical payload for the same role and purpose, ignoring review ids.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..org import Org
from .audit import read_records, verify_log


@dataclass
class RoleScore:
    role: str
    tokens: int = 0
    estimated_tokens: int = 0
    submissions: int = 0
    accepted_work: int = 0
    reviews_changed: int = 0
    outcomes: int = 0
    approval_only: int = 0
    repeated: int = 0
    changed: int = 0
    activities: int = 0
    token_share: float = 0.0
    unverified_usage_records: int = 0
    protection: str = ""
    evidence: list[str] = field(default_factory=list)

    def line(self) -> str:
        def frequency(count: int) -> str:
            return f"{count}/{self.activities}" if self.activities else "unobserved"

        return (
            f"{self.role} | {self.tokens:,}{'*' if self.unverified_usage_records else ''} "
            f"({self.token_share:.1%}) | {frequency(self.changed)} | "
            f"{self.accepted_work} | {self.reviews_changed} | {self.outcomes} | "
            f"{frequency(self.approval_only)} | {frequency(self.repeated)} | {self.protection or 'person decides'}"
        )


@dataclass
class Scorecard:
    roles: dict[str, RoleScore]
    runs: int = 0
    problems: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        return [
            f"Role scorecard from {self.runs} audit log(s), across all recorded runs.",
            "Role | Model tokens (share) | Changed/activities | Accepted work | Reviews leading to changes | "
            "Merged/posted | Approval only/activities | Repeated/activities | Protection",
            *(self.roles[role].line() for role in sorted(self.roles)),
            "Activities are hand-ins and distinct tracked outcomes. Changed counts first accepted canonical work, "
            "reviews followed by a different accepted result on the same task, and merged/posted outcomes. "
            "Approval and repetition can overlap. No recorded contribution is not proof a role is unnecessary.",
            "Tokens are input plus output, with cache reads/writes counted once "
            "when the audit records their convention. "
            "* marks unknown historical input subtotals; all shares are provisional then. "
            "Server-estimated tool traffic is separate from model tokens and their shares; "
            "self-reported usage is excluded. "
            f"Estimated tool traffic: {sum(r.estimated_tokens for r in self.roles.values()):,} tokens.",
            *self.problems,
        ]


def protected_roles(org: Org) -> dict[str, str]:
    protected = {g.reviewer: "gate: lighten through trust" for g in org.gates.values()}
    protected.update({d.qa_reviewer: "gate: lighten through trust" for d in org.departments.values() if d.qa_reviewer})
    protected[org.root_role] = "root: cannot remove"
    return protected


def _fingerprint(payload: dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in payload.items() if k not in ("subject_artifact_id", "gate")}, sort_keys=True)


def scorecard(home: Path, org: Org) -> Scorecard:
    """Read each plain or archived log once. Refuse corrupt evidence and report its absence."""
    report = Scorecard({ref: RoleScore(ref, protection=protected_roles(org).get(ref, "")) for ref in org.roles})
    seen: set[tuple[str, str, str]] = set()
    accepted_results: set[tuple[str, str, str]] = set()
    outcomes: dict[str, tuple[RoleScore, str]] = {}
    successful: set[str] = set()
    for folder in sorted((home / "runs").glob("*")):
        path = folder / "audit.jsonl"
        if not path.is_file() and not path.with_suffix(".jsonl.gz").is_file():
            continue
        try:
            checked = verify_log(path)
            records = read_records(path) if checked.ok else []
        except (OSError, EOFError, UnicodeError, ValueError, AttributeError, TypeError, zlib.error) as error:
            report.problems.append(f"Unverified: {folder.name} excluded: unreadable audit ({type(error).__name__}).")
            continue
        if not checked.ok:
            report.problems.append(f"Unverified: {folder.name} excluded: {checked.problem}.")
            continue
        report.runs += 1
        invocations: dict[str, dict[str, Any]] = {}
        results: dict[str, str] = {}
        requests: dict[str, list[tuple[RoleScore, str, str]]] = {}
        for index, record in enumerate(records):
            kind, data = record["kind"], record["data"]
            inner = data.get("kind")
            ref = f"runs/{folder.name}/audit.jsonl#{record['seq']} ({record['ts']})"
            if kind == "action" and inner == "invoke_role":
                invocations[data["invocation_id"]] = data
            elif kind == "event" and inner == "usage_recorded":
                role = data.get("role") or invocations.get(data.get("invocation_id"), {}).get("role")
                if not role:
                    role = "[unattributed]"
                row = report.roles.setdefault(role, RoleScore(role))
                if data.get("model") == "estimated by server":
                    tokens = int(data.get("input_tokens") or 0) + int(data.get("output_tokens") or 0)
                    row.estimated_tokens += tokens
                    row.evidence.append(f"{ref}: {tokens} estimated tool tokens")
                    continue
                inputs = int(data.get("input_tokens") or 0)
                cached = int(data.get("cached_input_tokens") or 0)
                created = int(data.get("cache_creation_input_tokens") or 0)
                convention = data.get("input_tokens_includes_cache")
                if convention is False:
                    inputs += cached + created
                if data.get("usage_status") in ("unavailable", "unverified") or (
                    convention is None and (inputs or cached or created)
                ):
                    row.unverified_usage_records += 1
                tokens = inputs + int(data.get("output_tokens") or 0)
                row.tokens += tokens
                row.evidence.append(f"{ref}: {tokens} tokens")
            elif kind == "event" and inner == "artifact_submitted":
                invocation = invocations.get(data["invocation_id"], {})
                role = data.get("actor") or invocation.get("role")
                if not role:
                    continue
                row = report.roles.setdefault(role, RoleScore(role))
                row.submissions += 1
                row.activities += 1
                # Old logs have no acceptance marker. A refused hand-in is followed by
                # invalid_output before the next event; keep it in cost, never contribution.
                accepted = True
                payload = data.get("payload") or {}
                for next_index in range(index + 1, len(records)):
                    next_record = records[next_index]
                    if next_record["kind"] == "event":
                        break
                    if next_record["kind"] in ("invalid_output", "rejected", "submission_refused"):
                        accepted = False
                        break
                    if next_record["kind"] == "submission_accepted":
                        payload = next_record["data"]["payload"]
                        break
                purpose = invocation.get("purpose", "")
                fingerprint = _fingerprint(payload)
                key = (role, purpose, fingerprint)
                repeated = key in seen
                seen.add(key)
                if repeated:
                    row.repeated += 1
                    row.evidence.append(f"{ref}: identical hand-in for {purpose}")
                if not accepted:
                    row.evidence.append(f"{ref}: refused hand-in")
                    continue
                task = invocation.get("task_id")
                if purpose in ("execute", "integrate"):
                    if key not in accepted_results:
                        row.accepted_work += 1
                        row.changed += 1
                        row.evidence.append(f"{ref}: accepted {purpose} work")
                    accepted_results.add(key)
                    if task:
                        waiting = []
                        for reviewer, before, request_ref in requests.get(task, []):
                            if fingerprint != before:
                                reviewer.reviews_changed += 1
                                reviewer.changed += 1
                                reviewer.evidence.append(f"{request_ref}: changes followed by {ref}")
                            else:
                                waiting.append((reviewer, before, request_ref))
                        requests[task] = waiting
                        results[task] = fingerprint
                elif purpose == "review":
                    if payload.get("decision") == "approve":
                        row.approval_only += 1
                        row.evidence.append(f"{ref}: approval only")
                    elif payload.get("decision") == "changes_requested" and task and task in results:
                        requests.setdefault(task, []).append((row, results[task], ref))
            elif kind == "outcome_tracked":
                role = data.get("role")
                if role and str(data["id"]) not in outcomes:
                    row = report.roles.setdefault(role, RoleScore(role))
                    row.activities += 1
                    outcomes[str(data["id"])] = (row, ref)
            elif kind == "outcome_settled" and data.get("status") in ("merged", "posted"):
                number = str(data["id"])
                if number in outcomes and number not in successful:
                    row, tracked_ref = outcomes[number]
                    row.outcomes += 1
                    row.changed += 1
                    row.evidence.append(f"{tracked_ref}: {data['status']} at {ref}")
                    successful.add(number)
    total = sum(r.tokens for r in report.roles.values())
    for row in report.roles.values():
        row.token_share = row.tokens / total if total else 0.0
        if row.unverified_usage_records:
            report.problems.append(
                f"Unverified: {row.role}: {row.unverified_usage_records} usage record(s) "
                "with counts unavailable or cache convention unverified; "
                "using recorded input subtotals. Token shares are provisional; see the audit evidence."
            )
    return report
