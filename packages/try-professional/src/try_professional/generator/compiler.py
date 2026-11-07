"""Turn a proposal into org.yaml and role files. Everything that matters for safety is decided here.

The model never chooses tools, gates, escalation, budgets, or endpoints. Executive, Legal, and QA come
from fixed definitions, and a proposal naming those departments has that part ignored.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ..org.loader import dump_org, validate_bundle
from ..org.model import Issue
from ..org.policy import EXECUTIVE, LEGAL, check_policy
from ..packs import DEFAULT_GATES, DEFAULT_PIPELINE, pack_department, pack_role
from .proposal import OrgProposal, ProposedRole

if TYPE_CHECKING:
    from .templates import Template

QA = "qa"
ROOT_ROLE = f"{EXECUTIVE}/ceo"
HEAD_REVIEWER = f"{EXECUTIVE}/chief-of-staff"
QA_REVIEWER = f"{QA}/reviewer"

_READ = ["workspace_read", "workspace_list", "memory_recall", "skill_search", "skill_get"]
_TOOLS = {
    "head": [*_READ, "workspace_write", "memory_write", "skill_propose"],
    "worker": [*_READ, "workspace_write", "memory_write"],
    "reviewer": _READ,
}
# Seniority a title states. Only used when a proposal doesn't say who heads a department.
_SENIORITY = (
    (3, frozenset({"chief", "ceo", "cto", "cfo", "coo", "cmo", "cpo", "cso", "president", "vp", "founder"})),
    (2, frozenset({"head", "director", "principal", "partner"})),
    (1, frozenset({"lead", "manager", "senior", "supervisor"})),
)
_RESEARCH_RULE = (
    " Name a source and date for anything you call current or trending. "
    "Your training data isn't a source: without both, label the claim unverified."
)
_REVIEW_RULE = " Reject work whose claims have no source, however good it reads."

_QA_DEPARTMENT = {
    "id": QA,
    "name": "Quality Assurance",
    "head": "qa-lead",
    "roles": [
        ProposedRole(
            id="qa-lead",
            name="QA Lead",
            kind="head",
            mission="Keep the company's work to its stated standard.",
            responsibilities=["Set review standards", "Review the reviewers' hard calls"],
            skill_needs=["define quality standards", "review work against acceptance criteria"],
            skill_categories=["testing", "quality"],
            outputs=["TaskReport"],
            instructions="Judge work against its definition of done, and say exactly what is missing.",
        ),
        ProposedRole(
            id="reviewer",
            name="Reviewer",
            kind="reviewer",
            mission="Review every department's work before it moves on.",
            responsibilities=["Check work against its definition of done", "Say exactly what needs to change"],
            skill_needs=["review work against acceptance criteria", "check facts and sources in a document"],
            skill_categories=["testing", "quality"],
            outputs=["TaskReport"],
            instructions="Approve only what the evidence supports. You didn't make this work, so don't defend it.",
        ),
    ],
}


class ProposalError(ValueError):
    """The proposal can't be compiled. The message is safe to show to the model."""


@dataclass
class CompiledOrg:
    org: dict[str, Any]
    roles: dict[str, dict[str, Any]]
    notes: list[str] = field(default_factory=list)

    def issues(self) -> list[Issue]:
        return [
            *[i for i in validate_bundle(self.org, self.roles) if i.severity == "error"],
            *check_policy(self.org, self.roles),
        ]

    def write(self, directory: Path) -> None:
        dump_org(self.org, directory)
        for ref, document in self.roles.items():
            department, role = ref.split("/", 1)
            path = directory / "roles" / department / f"{role}.yaml"
            path.parent.mkdir(parents=True, exist_ok=True)
            text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True, default_flow_style=False)
            path.write_text(text, encoding="utf-8", newline="\n")


def role_document(department: str, role: ProposedRole, head: str | None = None) -> dict[str, Any]:
    """A complete role file. The role's kind decides its tools."""
    is_head = role.kind == "head"
    is_reviewer = role.kind == "reviewer"
    outputs: list[str] = list(role.outputs)
    if is_head:
        outputs = ["TaskPlan", *outputs, "ReviewVerdict"]
    elif is_reviewer:
        outputs = ["ReviewVerdict", *outputs]
    tools = list(_TOOLS[role.kind])
    prompt = f"You are the {role.name}. {role.instructions.strip()}"
    if "ResearchReport" in role.outputs:
        tools.append("web_search")
        prompt += _RESEARCH_RULE
    if is_head or is_reviewer:
        prompt += _REVIEW_RULE
    return {
        "spec": "role/v1",
        "id": role.id,
        "title": role.name,
        "department": department,
        "mission": role.mission,
        "responsibilities": list(role.responsibilities),
        "inputs": ["WorkPackage", "TaskBrief"] if is_head else ["TaskBrief"],
        "outputs": outputs,
        "allowed_tools": tools,
        "allowed_skill_categories": list(role.skill_categories),
        "escalation": ROOT_ROLE if is_head else f"{department}/{head or role.id}",
        "reviewer": HEAD_REVIEWER if is_head else f"{department}/{head or role.id}",
        "model_tier": "standard",
        "skill_needs": list(role.skill_needs),
        "required_tools": ["workspace_read"] if is_reviewer else ["workspace_write"],
        "forbidden_tools": ["workspace_write"] if is_reviewer else [],
        "max_skills": 8,
        "system_prompt": prompt + "\n",
    }


def seniority(title: str) -> int:
    words = set(re.findall(r"[a-z]+", title.lower()))
    return next((rank for rank, names in _SENIORITY if words & names), 0)


def _settle_head(department: str, heads: list[str], members: dict[str, tuple[str, str]]) -> tuple[str, str | None]:
    """The head, and a note if the compiler chose. members maps role IDs to their title and kind.

    A proposal that names one head keeps it. Otherwise the role whose title is clearly most senior heads the
    department. Only a tie, or titles that say nothing, are ambiguous enough to send back.
    """
    if len(heads) == 1:
        return heads[0], None
    pool = heads or [name for name, (_, kind) in members.items() if kind != "reviewer"] or list(members)
    ranks = {name: seniority(members[name][0]) for name in pool}
    best = max(ranks.values(), default=0)
    winners = [name for name, rank in ranks.items() if rank == best]
    if len(winners) != 1 or (best == 0 and len(pool) > 1):
        named = ", ".join(pool) if heads else "none of its roles"
        raise ProposalError(
            f"department '{department}' needs exactly one head, and has {len(heads)} ({named}). "
            "Their titles don't settle it, so set kind to head on one role."
        )
    head = winners[0]
    title = members[head][0]
    if heads:
        return head, f"'{department}' named {len(heads)} heads, so {title}, the most senior title, heads it."
    return head, f"'{department}' named no head, so {title} heads it."


def _as_head(document: dict[str, Any]) -> dict[str, Any]:
    """A kept role promoted to head gets what a head gets, which the compiler decides and not the model."""
    tools = [tool for tool in _TOOLS["head"] if tool not in document["allowed_tools"]]
    outputs = [item for item in document["outputs"] if item not in ("TaskPlan", "ReviewVerdict")]
    return {
        **document,
        "inputs": ["WorkPackage", "TaskBrief"],
        "outputs": ["TaskPlan", *outputs, "ReviewVerdict"],
        "allowed_tools": [*document["allowed_tools"], *tools],
        "required_tools": ["workspace_write"],
        "forbidden_tools": [],
    }


def _as_worker(document: dict[str, Any]) -> dict[str, Any]:
    outputs = [item for item in document["outputs"] if item not in ("TaskPlan", "ReviewVerdict")]
    return {
        **document,
        "inputs": ["TaskBrief"],
        "outputs": outputs or ["TaskReport"],
        "allowed_tools": [tool for tool in document["allowed_tools"] if tool != "skill_propose"],
    }


def _locked_pack(name: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    department = copy.deepcopy(pack_department(name))
    roles = {f"{name}/{role_id}": copy.deepcopy(pack_role(name, role_id)) for role_id in department["roles"]}
    return department, roles


def _qa_department() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    head = str(_QA_DEPARTMENT["head"])
    members: list[ProposedRole] = _QA_DEPARTMENT["roles"]  # type: ignore[assignment]
    department = {
        "id": QA,
        "name": _QA_DEPARTMENT["name"],
        "head": head,
        "activation": "on_demand",
        "qa_gate": {"reviewer": head},
        "roles": [role.id for role in members],
    }
    roles = {f"{QA}/{role.id}": role_document(QA, role, head) for role in members}
    return department, roles


def compile_org(template: Template, proposal: OrgProposal) -> CompiledOrg:
    """Raises ProposalError for anything that can't become a company."""
    notes: list[str] = []
    departments: list[dict[str, Any]] = []
    roles: dict[str, dict[str, Any]] = {}
    needs_qa = False

    executive, executive_roles = _locked_pack(EXECUTIVE)
    departments.append(executive)
    roles.update(executive_roles)

    seen: set[str] = set()
    for item in proposal.departments:
        if item.id in (EXECUTIVE, LEGAL, QA):
            notes.append(f"'{item.id}' is fixed in every company, so the proposal's version was ignored.")
            continue
        if item.id in seen:
            raise ProposalError(f"department '{item.id}' is listed twice")
        seen.add(item.id)

        source = template.departments.get(item.from_template or "")
        if item.from_template and source is None:
            known = ", ".join(template.departments)
            raise ProposalError(f"'{item.from_template}' isn't a department in this template. It has: {known}")

        kept: dict[str, dict[str, Any]] = {}
        if source is not None:
            names = item.keep_roles or (list(source.roles) if not item.roles else [])
            for name in names:
                if name not in source.roles:
                    raise ProposalError(f"'{name}' isn't a role in the template's '{source.id}'")
                kept[name] = copy.deepcopy(source.roles[name])

        new_heads = [role.id for role in item.roles if role.kind == "head"]
        kept_head = source.head if source is not None and source.head in kept else None
        heads = [*new_heads, *([kept_head] if kept_head else [])]
        members = {name: (str(document.get("title") or name), "kept") for name, document in kept.items()}
        members.update({role.id: (role.name, role.kind) for role in item.roles})
        head, settled = _settle_head(item.id, heads, members)
        if settled:
            notes.append(settled)

        ordered = [head]
        for name, document in kept.items():
            is_head = name == head
            if is_head and name != kept_head:
                document = _as_head(document)
            elif not is_head and name == kept_head:
                document = _as_worker(document)
            document["department"] = item.id
            document["escalation"] = ROOT_ROLE if is_head else f"{item.id}/{head}"
            document["reviewer"] = HEAD_REVIEWER if is_head else f"{item.id}/{head}"
            roles[f"{item.id}/{name}"] = document
            if not is_head:
                ordered.append(name)
        for role in item.roles:
            if role.id in kept:
                raise ProposalError(f"role '{role.id}' is both kept and new in '{item.id}'")
            if f"{item.id}/{role.id}" in roles:
                raise ProposalError(f"role '{role.id}' is listed twice in '{item.id}'")
            if (role.id == head) != (role.kind == "head"):
                role = role.model_copy(update={"kind": "head" if role.id == head else "worker"})
            roles[f"{item.id}/{role.id}"] = role_document(item.id, role, head)
            if role.id != head:
                ordered.append(role.id)

        # A pack's own QA reviewer stays if it survived. Everything else goes to the QA department.
        own_reviewer = source.qa_reviewer if source is not None else None
        if own_reviewer and (own_reviewer in ordered or "/" in own_reviewer):
            qa_gate = {"reviewer": own_reviewer}
        else:
            qa_gate = {"reviewer": QA_REVIEWER}
            needs_qa = True
        departments.append(
            {
                "id": item.id,
                "name": item.name,
                "head": head,
                "activation": "on_demand",
                "qa_gate": qa_gate,
                "roles": ordered,
            }
        )

    if not seen:
        raise ProposalError("a company needs at least one department that does the work")

    if needs_qa:
        qa, qa_roles = _qa_department()
        departments.append(qa)
        roles.update(qa_roles)
    legal, legal_roles = _locked_pack(LEGAL)
    departments.append(legal)
    roles.update(legal_roles)

    pipeline = copy.deepcopy(DEFAULT_PIPELINE)
    pipeline["working_language"] = proposal.working_language
    company: dict[str, Any] = {"name": proposal.company_name}
    business = proposal.business.model_dump(exclude_defaults=True)
    if business:
        company["business"] = business
    org = {
        "spec": "org/v1",
        "company": company,
        "root_role": ROOT_ROLE,
        "departments": departments,
        "gates": copy.deepcopy(DEFAULT_GATES),
        "pipeline": pipeline,
    }
    return CompiledOrg(org, roles, notes)
