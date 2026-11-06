"""Load and validate an organization (spec org/v1, levels 1 and 2)."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .model import Department, Gate, Issue, Org, PipelineConfig, Role

SCHEMA_CODE = "SCHEMA"


class OrgValidationError(ValueError):
    """Raised when an organization fails validation."""

    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        super().__init__("\n".join(str(i) for i in issues))


def _schema(name: str) -> dict[str, Any]:
    text = resources.files("try_professional._spec").joinpath(name).read_text(encoding="utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded


def _schema_issues(document: Any, schema_name: str, label: str) -> list[Issue]:
    validator = jsonschema.Draft202012Validator(_schema(schema_name))
    issues = []
    for error in sorted(validator.iter_errors(document), key=lambda e: list(map(str, e.path))):
        path = "/".join(str(p) for p in error.absolute_path) or "(root)"
        issues.append(Issue(SCHEMA_CODE, f"{label} at {path}: {error.message}"))
    return issues


def _full_ref(department: str, ref: str) -> str:
    return ref if "/" in ref else f"{department}/{ref}"


def validate_bundle(org: dict[str, Any], roles: dict[str, dict[str, Any]]) -> list[Issue]:
    """Return every issue found. Level 2 runs only if level 1 is clean."""
    issues = _schema_issues(org, "org-v1.schema.json", "org.yaml")
    for ref, role in sorted(roles.items()):
        issues += _schema_issues(role, "role-v1.schema.json", f"role {ref}")
    if issues:
        return issues
    return _semantic_issues(org, roles)


def _semantic_issues(org: dict[str, Any], roles: dict[str, dict[str, Any]]) -> list[Issue]:
    issues: list[Issue] = []
    departments = org["departments"]
    root = org["root_role"]

    seen_departments: set[str] = set()
    listed: set[str] = set()
    for department in departments:
        dep_id = department["id"]
        if dep_id in seen_departments:
            issues.append(Issue("ORG001", f"duplicate department '{dep_id}'"))
        seen_departments.add(dep_id)

        seen_roles: set[str] = set()
        for role_id in department["roles"]:
            if role_id in seen_roles:
                issues.append(Issue("ORG002", f"duplicate role '{role_id}' in '{dep_id}'"))
            seen_roles.add(role_id)
            listed.add(f"{dep_id}/{role_id}")
        if department["head"] not in seen_roles:
            issues.append(Issue("ORG003", f"head '{department['head']}' is not listed in '{dep_id}'"))

    for ref in sorted(listed - set(roles)):
        issues.append(Issue("ORG004", f"role '{ref}' has no role file"))
    for ref, role in sorted(roles.items()):
        declared = f"{role['department']}/{role['id']}"
        if ref not in listed or declared != ref:
            issues.append(Issue("ORG005", f"role file '{ref}' declares '{declared}' and must be listed there"))

    heads = {f"{d['id']}/{d['head']}" for d in departments}
    if root not in heads:
        issues.append(Issue("ORG006", f"root_role '{root}' is not the head of a department"))
    root_department = root.split("/", 1)[0]

    known = set(roles)
    for ref, role in sorted(roles.items()):
        escalation = role["escalation"]
        if escalation == "human":
            if ref != root:
                issues.append(Issue("ORG009", f"only the root role may escalate to human, not '{ref}'"))
        else:
            if ref == root:
                issues.append(Issue("ORG009", "the root role must escalate to human"))
            if escalation not in known:
                issues.append(Issue("ORG007", f"'{ref}' escalates to unknown role '{escalation}'"))
            elif ref not in heads and escalation.split("/", 1)[0] != ref.split("/", 1)[0]:
                issues.append(Issue("ORG013", f"non-head '{ref}' must escalate within its department"))
            elif ref in heads and ref != root and escalation.split("/", 1)[0] != root_department:
                issues.append(Issue("ORG014", f"head '{ref}' must escalate to the root department"))

        allowed_tools = set(role["allowed_tools"])
        if allowed_tools & set(role.get("forbidden_tools", [])):
            issues.append(Issue("ORG016", f"'{ref}' both allows and forbids a tool"))
        if set(role.get("required_tools", [])) - allowed_tools:
            issues.append(Issue("ORG017", f"'{ref}' requires a tool it isn't allowed"))

        if role["reviewer"] not in known:
            issues.append(Issue("ORG010", f"'{ref}' has unknown reviewer '{role['reviewer']}'"))
        elif role["reviewer"] == ref:
            issues.append(Issue("ORG011", f"'{ref}' cannot review itself"))

    for ref in sorted(known):
        path = [ref]
        current = ref
        while current != root:
            next_ref = roles.get(current, {}).get("escalation")
            if next_ref is None or next_ref == "human" or next_ref not in known:
                issues.append(Issue("ORG008", f"escalation from '{ref}' does not reach the root role"))
                break
            if next_ref in path:
                issues.append(Issue("ORG008", f"escalation cycle: {' -> '.join([*path, next_ref])}"))
                break
            path.append(next_ref)
            current = next_ref

    for department in departments:
        qa_gate = department.get("qa_gate")
        if qa_gate and _full_ref(department["id"], qa_gate["reviewer"]) not in known:
            issues.append(Issue("ORG012", f"'{department['id']}' has unknown QA reviewer"))
        if department["id"] == root_department and department.get("activation", "on_demand") != "always":
            issues.append(Issue("ORG015", "the root role's department must have activation: always"))

    for name, gate in org.get("gates", {}).items():
        if name.startswith("x-"):
            continue
        if gate["reviewer"] not in known:
            issues.append(Issue("ORG101", f"gate '{name}' reviewer '{gate['reviewer']}' is unavailable", "warning"))

    known_departments = {d["id"] for d in departments}
    for target in org.get("llm_overrides", {}):
        if target.startswith("x-"):
            continue
        if target not in known and target not in known_departments:
            issues.append(Issue("ORG102", f"llm_overrides names '{target}', which is not installed", "warning"))
    return issues


def load_bundle(org: dict[str, Any], roles: dict[str, dict[str, Any]]) -> Org:
    """Validate raw documents and build a typed Org. Warnings don't fail."""
    issues = validate_bundle(org, roles)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise OrgValidationError(errors)

    departments = {}
    for item in org["departments"]:
        qa_gate = item.get("qa_gate")
        departments[item["id"]] = Department(
            id=item["id"],
            head=item["head"],
            roles=tuple(item["roles"]),
            name=item.get("name"),
            activation=item.get("activation", "on_demand"),
            qa_reviewer=_full_ref(item["id"], qa_gate["reviewer"]) if qa_gate else None,
        )

    typed_roles = {
        ref: Role(
            id=data["id"],
            department=data["department"],
            mission=data["mission"],
            responsibilities=tuple(data["responsibilities"]),
            inputs=tuple(data["inputs"]),
            outputs=tuple(data["outputs"]),
            allowed_tools=tuple(data["allowed_tools"]),
            allowed_skill_categories=tuple(data["allowed_skill_categories"]),
            escalation=data["escalation"],
            reviewer=data["reviewer"],
            system_prompt=data["system_prompt"],
            model_tier=data.get("model_tier", "standard"),
            skill_needs=tuple(data.get("skill_needs", ())),
            required_tools=tuple(data.get("required_tools", ())),
            forbidden_tools=tuple(data.get("forbidden_tools", ())),
            max_skills=data.get("max_skills", 8),
            title=data.get("title", ""),
        )
        for ref, data in roles.items()
    }

    gates = {
        name: Gate(
            name=name,
            reviewer=gate["reviewer"],
            triggers=tuple(gate["triggers"]),
            on_unavailable=gate.get("on_unavailable", "block"),
        )
        for name, gate in org.get("gates", {}).items()
        if not name.startswith("x-")
    }

    pipeline = org.get("pipeline", {})
    return Org(
        company_name=org["company"]["name"],
        root_role=org["root_role"],
        departments=departments,
        roles=typed_roles,
        gates=gates,
        pipeline=PipelineConfig(
            max_parallel_tasks=pipeline.get("max_parallel_tasks", 4),
            max_rework=pipeline.get("max_rework", 2),
            max_invalid_outputs=pipeline.get("max_invalid_outputs", 2),
            working_language=pipeline.get("working_language", "auto"),
            max_tokens_per_run=pipeline.get("budget", {}).get("max_tokens_per_run"),
        ),
        raw=org,
        raw_roles=roles,
        llm_overrides={k: v for k, v in org.get("llm_overrides", {}).items() if not k.startswith("x-")},
    )


def read_documents(directory: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    org_file = directory / "org.yaml"
    if not org_file.is_file():
        raise FileNotFoundError(f"no org.yaml in {directory}")
    org = yaml.safe_load(org_file.read_text(encoding="utf-8"))
    roles: dict[str, dict[str, Any]] = {}
    roles_dir = directory / "roles"
    if roles_dir.is_dir():
        for path in sorted(roles_dir.glob("*/*.yaml")):
            roles[f"{path.parent.name}/{path.stem}"] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return org, roles


def load_org(directory: Path) -> Org:
    org, roles = read_documents(directory)
    return load_bundle(org, roles)


def dump_org(org: dict[str, Any], directory: Path) -> None:
    """Write org.yaml. YAML comments are not preserved; x- keys are, because they are data."""
    directory.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(org, sort_keys=False, allow_unicode=True, default_flow_style=False)
    (directory / "org.yaml").write_text(text, encoding="utf-8", newline="\n")
