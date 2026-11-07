"""Typed view of a validated organization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["error", "warning"]
FLAG_NAMES = ("public_facing", "touches_license", "touches_personal_data")


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    severity: Severity = "error"

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


_ACRONYMS = frozenset(
    {"ai", "api", "ceo", "cfo", "cmo", "coo", "cpo", "cso", "cto", "hr", "it", "pr", "qa", "seo", "ui", "ux"}
)


@dataclass(frozen=True)
class Role:
    id: str
    department: str
    mission: str
    responsibilities: tuple[str, ...]
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    allowed_skill_categories: tuple[str, ...]
    escalation: str
    reviewer: str
    system_prompt: str
    model_tier: str = "standard"
    skill_needs: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    max_skills: int = 8
    title: str = ""

    @property
    def ref(self) -> str:
        return f"{self.department}/{self.id}"

    @property
    def display_name(self) -> str:
        """A role with no title still reads as a name, and never as a raw ID."""
        if self.title:
            return self.title
        words = self.id.replace("_", "-").split("-")
        return " ".join(word.upper() if word in _ACRONYMS else word.capitalize() for word in words)


@dataclass(frozen=True)
class Department:
    id: str
    head: str
    roles: tuple[str, ...]
    name: str | None = None
    activation: str = "on_demand"
    qa_reviewer: str | None = None

    @property
    def head_ref(self) -> str:
        return f"{self.id}/{self.head}"


@dataclass(frozen=True)
class Gate:
    name: str
    reviewer: str
    triggers: tuple[str, ...]
    on_unavailable: str = "block"


@dataclass(frozen=True)
class PipelineConfig:
    max_parallel_tasks: int = 4
    max_rework: int = 2
    max_invalid_outputs: int = 2
    working_language: str = "auto"
    max_tokens_per_run: int | None = None


@dataclass(frozen=True)
class BusinessContext:
    """What the company does, for telling which skills belong to its trade. Empty means unknown."""

    description: str = ""
    industry: str = ""
    products: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return " ".join(part for part in (self.description, self.industry, *self.products, *self.channels) if part)


@dataclass(frozen=True)
class Org:
    company_name: str
    root_role: str
    departments: dict[str, Department]
    roles: dict[str, Role]
    gates: dict[str, Gate]
    pipeline: PipelineConfig
    raw: dict[str, Any] = field(default_factory=dict, compare=False)
    raw_roles: dict[str, dict[str, Any]] = field(default_factory=dict, compare=False)
    llm_overrides: dict[str, dict[str, Any]] = field(default_factory=dict, compare=False)
    business: BusinessContext = field(default_factory=BusinessContext)

    def llm_override_for(self, ref: str) -> dict[str, Any]:
        """Merge overrides, field by field: the role's wins over its department's."""
        department = ref.split("/", 1)[0]
        return {**self.llm_overrides.get(department, {}), **self.llm_overrides.get(ref, {})}

    def role(self, ref: str) -> Role:
        return self.roles[ref]

    def has_role(self, ref: str) -> bool:
        return ref in self.roles

    def department_of(self, ref: str) -> Department:
        return self.departments[ref.split("/", 1)[0]]

    def is_head(self, ref: str) -> bool:
        department, _, role = ref.partition("/")
        found = self.departments.get(department)
        return found is not None and found.head == role

    def is_senior(self, ref: str) -> bool:
        """The root role, or a department head."""
        return ref == self.root_role or self.is_head(ref)

    def direct_reports(self, ref: str) -> list[str]:
        """Who escalates to this role. Reporting lines come only from the role files."""
        return sorted(other for other, role in self.roles.items() if role.escalation == ref)

    def always_active(self) -> list[str]:
        return [d.id for d in self.departments.values() if d.activation == "always"]

    def resolve_reviewer(self, owner_ref: str) -> str:
        """Return the reviewer for work owned by owner_ref. Never returns the owner.

        The department's QA reviewer applies first. A role that is itself the QA
        reviewer falls back to the reviewer named in its role file, which spec
        rule ORG011 guarantees is someone else.
        """
        qa_reviewer = self.department_of(owner_ref).qa_reviewer
        if qa_reviewer and qa_reviewer != owner_ref:
            return qa_reviewer
        return self.role(owner_ref).reviewer
