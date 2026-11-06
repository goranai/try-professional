"""Industry templates: the starting point for every generated company.

A template's departments use the same form as an OrgProposal, so a contributor writes one file, and
it can't express anything a model couldn't. general-company is the exception: it comes from the packs.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Any, Protocol

import numpy as np
import yaml

from ..org.policy import EXECUTIVE, LEGAL
from ..packs import pack_department, pack_role
from .compiler import ProposalError, role_document
from .proposal import OrgProposal, ProposedDepartment

GENERAL = "general-company"
LOCKED_DEPARTMENTS = frozenset({EXECUTIVE, LEGAL, "qa"})


class Embedder(Protocol):
    def embed_query(self, text: str) -> Any: ...


@dataclass(frozen=True)
class TemplateDepartment:
    id: str
    name: str
    head: str
    roles: dict[str, dict[str, Any]]
    qa_reviewer: str | None = None


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    description: str
    examples: tuple[str, ...]
    departments: dict[str, TemplateDepartment]

    def match_text(self) -> list[str]:
        return [self.description, *self.examples]

    def summary(self) -> list[dict[str, Any]]:
        """What the Architect sees: enough to adapt it, and small, because every call resends it."""
        return [
            {
                "id": department.id,
                "name": department.name,
                "roles": [
                    {
                        "id": role_id,
                        "head": role_id == department.head,
                        "mission": doc["mission"],
                    }
                    for role_id, doc in department.roles.items()
                ],
            }
            for department in self.departments.values()
        ]

    def keep_all(self, company_name: str) -> OrgProposal:
        """The template as it stands. Used with no model, and by the scripted Architect."""
        return OrgProposal(
            company_name=company_name,
            rationale=f"The {self.name} template, unchanged.",
            departments=[
                ProposedDepartment(id=d.id, name=d.name, from_template=d.id) for d in self.departments.values()
            ],
        )


def _root() -> Any:
    return resources.files("try_professional._templates")


def template_ids() -> list[str]:
    names = [p.name for p in _root().iterdir() if p.is_dir() and p.joinpath("template.yaml").is_file()]
    return sorted(names)


def _pack_departments() -> dict[str, TemplateDepartment]:
    from ..packs import available_packs

    departments = {}
    for name in available_packs():
        if name in LOCKED_DEPARTMENTS:
            continue
        pack = pack_department(name)
        reviewer = (pack.get("qa_gate") or {}).get("reviewer")
        departments[name] = TemplateDepartment(
            id=name,
            name=pack.get("name", name),
            head=pack["head"],
            roles={role_id: pack_role(name, role_id) for role_id in pack["roles"]},
            qa_reviewer=reviewer,
        )
    return departments


def load_template(template_id: str) -> Template:
    if template_id not in template_ids():
        raise KeyError(f"unknown template '{template_id}'. Available: {', '.join(template_ids())}")
    data = yaml.safe_load(_root().joinpath(template_id).joinpath("template.yaml").read_text(encoding="utf-8"))
    if data.get("source") == "packs":
        departments = _pack_departments()
    else:
        departments = {}
        for item in data["departments"]:
            parsed = ProposedDepartment.model_validate(item)
            heads = [r for r in parsed.roles if r.kind == "head"]
            if len(heads) != 1:
                raise ProposalError(f"template department '{parsed.id}' needs exactly one head")
            departments[parsed.id] = TemplateDepartment(
                id=parsed.id,
                name=parsed.name,
                head=heads[0].id,
                roles={role.id: role_document(parsed.id, role) for role in parsed.roles},
            )
    return Template(
        id=data["id"],
        name=data["name"],
        description=data["description"],
        examples=tuple(data.get("examples", ())),
        departments=departments,
    )


def rank_templates(description: str, embedder: Embedder, *, exclude: tuple[str, ...] = ()) -> list[tuple[str, float]]:
    """Every template, closest first. Runs locally: the description goes nowhere."""
    query = embedder.embed_query(description)
    ranked = []
    for template_id in template_ids():
        if template_id in exclude:
            continue
        texts = load_template(template_id).match_text()
        score = max(float(np.dot(query, embedder.embed_query(text))) for text in texts)
        ranked.append((template_id, score))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked
