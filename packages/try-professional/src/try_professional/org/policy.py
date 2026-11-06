"""What a generated company must have. Separate from the spec, which stays permissive.

org/v1 allows a company with no Legal department, because departments are removable. Nothing is
activated from `trypro init "..."` unless it also passes this. Someone can still change a company
by hand afterwards.
"""

from __future__ import annotations

from typing import Any

from .model import Issue

EXECUTIVE = "executive"
LEGAL = "legal"
REQUIRED_TRIGGERS = frozenset({"public_facing", "touches_license", "touches_personal_data"})
MAX_ROLES = 25
# Everything a role can be given in a generated company. A shell isn't here, and neither is anything unknown.
KNOWN_TOOLS = frozenset(
    {
        "workspace_read",
        "workspace_write",
        "workspace_list",
        "memory_recall",
        "memory_write",
        "skill_search",
        "skill_get",
        "skill_propose",
        "web_search",
    }
)


def check_policy(org: dict[str, Any], roles: dict[str, dict[str, Any]]) -> list[Issue]:
    issues: list[Issue] = []
    departments = {d["id"]: d for d in org.get("departments", [])}
    root = str(org.get("root_role", ""))

    if root.split("/", 1)[0] != EXECUTIVE or departments.get(EXECUTIVE, {}).get("activation") != "always":
        issues.append(Issue("GEN001", "the root role must be in an always-active Executive department"))

    if LEGAL not in departments:
        issues.append(Issue("GEN002", "a generated company must have a Legal department"))
    gate = (org.get("gates") or {}).get(LEGAL) or {}
    if (
        set(gate.get("triggers", [])) != REQUIRED_TRIGGERS
        or gate.get("on_unavailable", "block") != "block"
        or str(gate.get("reviewer", "")).split("/", 1)[0] != LEGAL
    ):
        issues.append(Issue("GEN003", "the legal gate must keep all three triggers, a Legal reviewer, and never waive"))

    for department_id, department in departments.items():
        if department_id != EXECUTIVE and not department.get("qa_gate"):
            issues.append(Issue("GEN004", f"department '{department_id}' has no QA gate"))

    if len(roles) > MAX_ROLES:
        issues.append(Issue("GEN005", f"{len(roles)} roles is over the limit of {MAX_ROLES}"))

    for ref, role in sorted(roles.items()):
        unknown = sorted(set(role.get("allowed_tools", [])) - KNOWN_TOOLS)
        if unknown:
            issues.append(Issue("GEN006", f"'{ref}' has a tool a generated company can't have: {', '.join(unknown)}"))

    if {key for key in org.get("llm_overrides", {}) if not key.startswith("x-")}:
        issues.append(Issue("GEN007", "a generated company can't name its own model endpoints or keys"))

    budget = (org.get("pipeline") or {}).get("budget") or {}
    if not budget.get("max_tokens_per_run"):
        issues.append(Issue("GEN008", "a generated company must have a token ceiling"))
    return issues
