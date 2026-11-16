"""Whether a roster proposal preserves the root, gates and reporting hierarchy."""

from __future__ import annotations

from ..org import Org
from .artifacts import OrgChange
from .scorecard import protected_roles


def org_change_problem(changes: list[OrgChange], org: Org) -> str | None:
    """A proposed role or department a person could approve as written. Nothing is changed here."""
    for change in changes:
        if change.kind in ("retire_role", "merge_role"):
            ref = f"{change.department}/{change.role_id}"
            if ref not in org.roles:
                return f"{ref} isn't a role here"
            if ref in protected_roles(org):
                return f"Cannot remove {ref}: {protected_roles(org)[ref]}"
            if change.kind == "retire_role" and org.is_head(ref):
                return "A department head must be merged into another role in its department"
            if change.kind == "merge_role":
                if change.into not in org.roles or change.into == ref:
                    return "merge_role needs a different existing role in into"
                if change.into.split("/", 1)[0] != change.department:
                    return "Merge within the department to preserve reporting lines"
        elif change.kind == "add_role":
            if change.department not in org.departments:
                known = ", ".join(org.departments)
                return f"add_role needs an existing department, one of: {known}. For a new one, use add_department"
            if f"{change.department}/{change.role_id}" in org.roles:
                return f"{change.department}/{change.role_id} already exists. Propose only what the company lacks"
        elif change.department in org.departments:
            return f"Department '{change.department}' already exists. Use add_role to add to it"
    return None
