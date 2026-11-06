"""What the Org Architect may say about a company.

This shape is the main safety decision here. Generated text becomes system prompts, and org.yaml can
name endpoints and keys, so the model gets a narrow form to fill in. It has no field for tools,
gates, budgets, escalation, or model settings, and anything extra is rejected.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,39}$")]
Line = Annotated[str, StringConstraints(min_length=1, max_length=160)]
Phrase = Annotated[str, StringConstraints(min_length=1, max_length=90)]
WorkOutput = Literal["PublicContent", "ResearchReport", "TaskReport", "CodeChange"]
_LANGUAGE = re.compile(r"^(auto|[a-z]{2,3}(-[A-Za-z0-9]{2,8})*)$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposedRole(_Strict):
    id: Identifier
    name: str = Field(min_length=1, max_length=60)
    kind: Literal["head", "worker", "reviewer"]
    mission: str = Field(min_length=1, max_length=200)
    responsibilities: list[Line] = Field(min_length=1, max_length=6)
    skill_needs: list[Phrase] = Field(
        min_length=1, max_length=6, description="Short English phrases naming capabilities this role's skills provide"
    )
    skill_categories: list[Identifier] = Field(min_length=1, max_length=5)
    outputs: list[WorkOutput] = Field(min_length=1, max_length=3)
    instructions: str = Field(
        min_length=1, max_length=400, description="How this role does good work. One to three sentences"
    )


class ProposedDepartment(_Strict):
    id: Identifier
    name: str = Field(min_length=1, max_length=60)
    from_template: Identifier | None = Field(None, description="The template department to start from, if any")
    keep_roles: list[Identifier] = Field(
        [], description="Roles to keep from that department. Leave empty, with no new roles, to keep them all"
    )
    roles: list[ProposedRole] = Field([], max_length=8, description="New roles")

    @model_validator(mode="after")
    def _has_roles(self) -> ProposedDepartment:
        if not self.from_template and not self.roles:
            raise ValueError(f"department '{self.id}' needs roles, or a from_template")
        if self.keep_roles and not self.from_template:
            raise ValueError(f"department '{self.id}' has keep_roles, but no from_template")
        return self


class OrgProposal(_Strict):
    company_name: str = Field(min_length=1, max_length=80)
    rationale: str = Field("", max_length=400, description="Why this structure suits the business, in one sentence")
    working_language: str = Field(
        "auto", description="BCP 47 tag if this company always works in one language. Otherwise auto"
    )
    departments: list[ProposedDepartment] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def _language_is_a_tag(self) -> OrgProposal:
        if not _LANGUAGE.match(self.working_language):
            raise ValueError("working_language must be 'auto' or a BCP 47 tag")
        return self
