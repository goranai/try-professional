"""What a generated company must have. Separate from the spec, which stays permissive.

Legal is optional: a business that needs it gets a Legal department and its gate together. Nothing is
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

    # Legal is optional. A company that has it keeps its whole gate, and one that doesn't has no legal gate.
    gate = (org.get("gates") or {}).get(LEGAL) or {}
    if (LEGAL in departments) != bool(gate):
        issues.append(Issue("GEN002", "a Legal department and its gate come together, or not at all"))
    if LEGAL in departments and (
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
    for name in ("max_tokens_per_run", "max_tokens_per_small_command"):
        value = budget.get(name)
        # No limit is fine. A limit that's set has to be a real one.
        if value is not None and (not isinstance(value, int) or value <= 0):
            issues.append(Issue("GEN008", f"{name} must be a positive number of tokens, or left out for no limit"))
    return issues
