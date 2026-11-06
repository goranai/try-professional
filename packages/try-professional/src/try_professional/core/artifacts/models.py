"""Artifact envelope and payload models.

These models are the source of truth. JSON Schema is generated from them into
spec/artifacts-v1/, and a test fails if the two drift.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

SCHEMA_VERSION = "1"
Attestation = Literal["runtime", "claimed"]
NonEmpty = Field(min_length=1)


WORK_OUTPUT_TYPES = ("CodeChange", "PublicContent", "ResearchReport", "TaskReport")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Flags(_Strict):
    """Gate triggers. A flag can be added later but never cleared."""

    public_facing: bool = False
    touches_license: bool = False
    touches_personal_data: bool = False

    def union(self, other: Flags) -> Flags:
        return Flags(
            public_facing=self.public_facing or other.public_facing,
            touches_license=self.touches_license or other.touches_license,
            touches_personal_data=self.touches_personal_data or other.touches_personal_data,
        )

    def active(self) -> list[str]:
        return [name for name, value in self.model_dump().items() if value]


class GoalCharter(_Strict):
    original_goal: str = NonEmpty
    restated_goal: str = NonEmpty
    user_language: str = Field(min_length=2, description="BCP 47 tag of the language the goal was written in")
    constraints: list[str] = []
    success_criteria: list[str] = Field(min_length=1)
    size: Literal["small", "large"] = "large"
    route_department: str = ""
    route_role: str = Field("", description="department/role")


class DepartmentActivation(_Strict):
    id: str = NonEmpty
    reason: str = NonEmpty


class WorkPackage(_Strict):
    id: str = NonEmpty
    title: str = NonEmpty
    owner_department: str = NonEmpty
    depends_on: list[str] = []
    acceptance: list[str] = Field(min_length=1)
    flags: Flags = Flags()
    token_budget: int = Field(0, ge=0)


class Plan(_Strict):
    activated_departments: list[DepartmentActivation] = Field(min_length=1)
    work_packages: list[WorkPackage] = Field(min_length=1)


class TaskSpec(_Strict):
    id: str = NonEmpty
    title: str = NonEmpty
    owner_role: str = Field(min_length=1, description="department/role. Must be in this department")
    goal: str = NonEmpty
    context: str = ""
    inputs: list[str] = []
    expected_output: str = NonEmpty
    relevant_memory: list[str] = []
    relevant_skills: list[str] = []
    definition_of_done: list[str] = Field(min_length=1)
    depends_on: list[str] = []
    output_type: str = NonEmpty
    flags: Flags = Flags()


class TaskPlan(_Strict):
    """A department head's breakdown of one work package."""

    work_package_id: str = NonEmpty
    tasks: list[TaskSpec] = Field(min_length=1)
    adopt_lessons: list[int] = []
    drop_lessons: list[int] = []


class TaskBrief(_Strict):
    """Issued by the orchestrator from a TaskPlan. The reviewer comes from the org, not the plan."""

    task_id: str = NonEmpty
    work_package_id: str = NonEmpty
    title: str = NonEmpty
    owner_role: str = NonEmpty
    reviewer_role: str = NonEmpty
    goal: str = NonEmpty
    context: str = ""
    inputs: list[str] = []
    expected_output: str = NonEmpty
    relevant_memory: list[str] = []
    relevant_skills: list[str] = []
    definition_of_done: list[str] = Field(min_length=1)
    depends_on: list[str] = []
    output_type: str = NonEmpty


class SkillCandidate(_Strict):
    skill_id: str = NonEmpty
    score: float
    trust: Literal["verified", "indexed", "quarantined"]
    category: str = ""
    description: str = ""


class SkillResolution(_Strict):
    query: str = NonEmpty
    candidates: list[SkillCandidate] = []
    selected: list[str] = []
    reason: str = ""


class DoneCriterion(_Strict):
    criterion: str = NonEmpty
    met: bool
    evidence: str = ""


class TaskReport(_Strict):
    summary: str = NonEmpty
    outputs: list[str] = []
    done_criteria_met: list[DoneCriterion] = []
    open_issues: list[str] = []


class ChangedFile(_Strict):
    path: str = NonEmpty
    sha256: str = ""


class TestRun(_Strict):
    __test__ = False  # not a pytest class
    command: str = NonEmpty
    result: str = NonEmpty


class CodeChange(_Strict):
    summary: str = NonEmpty
    files: list[ChangedFile] = []
    tests: list[TestRun] = []
    done_criteria_met: list[DoneCriterion] = []


class Claim(_Strict):
    claim: str = NonEmpty
    source: str = ""


class PublicContent(_Strict):
    audience: str = NonEmpty
    channel: str = NonEmpty
    locale: str = NonEmpty
    body: str = NonEmpty
    claims: list[Claim] = []
    done_criteria_met: list[DoneCriterion] = []


# Wording that presents a claim as current, in English and Indonesian to start with. A model can
# still avoid these words, so this catches the ordinary case, and QA reads the rest.
_CURRENT_CLAIM = re.compile(
    r"\b(this|last|past) (week|month|year)\b|\btoday\b|\bright now\b|\bcurrent(ly)?\b|\blatest\b|\brecent(ly)?\b"
    r"|\btrend(s|ing)?\b|\bgoing viral\b|\bnowadays\b"
    r"|\bminggu ini\b|\bbulan ini\b|\bhari ini\b|\bsaat ini\b|\bsekarang\b|\bterkini\b|\bterbaru\b"
    r"|\btren\b|\bsedang viral\b|\blagi viral\b",
    re.IGNORECASE,
)
_THIS_WEEK_TREND = re.compile(
    r"this week['\u2019]?s? trend|trend(s|ing)? (of |for )?this week|tren minggu ini", re.IGNORECASE
)


def _is_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


class Finding(_Strict):
    statement: str = NonEmpty
    confidence: Literal["low", "medium", "high"]
    sources: list[str] = []
    source_date: str = Field("", description="Date of the source, YYYY-MM-DD")
    is_current: bool = Field(False, description="True if this claims something is current or trending")
    unverified: bool = Field(False, description="True if a current claim has no source and date")

    @model_validator(mode="after")
    def _current_claims_need_evidence(self) -> Finding:
        """Stale or invented trends look exactly like real ones, so a current claim must show its evidence."""
        if not self.is_current and not _CURRENT_CLAIM.search(self.statement):
            return self
        evidenced = bool(self.sources) and _is_date(self.source_date)
        if not evidenced and not self.unverified:
            raise ValueError(
                "A current or trend claim needs sources and a source_date (YYYY-MM-DD). "
                "If you have neither, set unverified to true."
            )
        if (self.unverified or not evidenced) and _THIS_WEEK_TREND.search(self.statement):
            raise ValueError("An unverified claim can't be called this week's trend. Reword it.")
        return self


class ResearchReport(_Strict):
    question: str = NonEmpty
    findings: list[Finding] = []
    done_criteria_met: list[DoneCriterion] = []


class ReviewIssue(_Strict):
    severity: Literal["minor", "major", "blocker"]
    description: str = NonEmpty


class ReviewVerdict(_Strict):
    gate: str = NonEmpty
    subject_artifact_id: str = NonEmpty
    decision: Literal["approve", "changes_requested", "block"]
    issues: list[ReviewIssue] = []


class Handoff(_Strict):
    """A logged request between department heads. The only cross-department channel."""

    from_department: str = NonEmpty
    to_department: str = NonEmpty
    request: str = NonEmpty
    acceptance: list[str] = Field(min_length=1)
    artifact_refs: list[str] = []


class Escalation(_Strict):
    reason: str = NonEmpty
    options: list[str] = []
    recommendation: str = ""
    blocking: bool = True


class EscalationResolution(_Strict):
    decision: Literal["resume", "abandon", "escalate_further", "amend_plan"]
    guidance: str = ""
    activate_departments: list[DepartmentActivation] = []
    added_work_packages: list[WorkPackage] = []


class CriterionResult(_Strict):
    criterion: str = NonEmpty
    met: bool
    evidence: str = ""


class FinalDeliverable(_Strict):
    summary_in_user_language: str = NonEmpty
    artifact_refs: list[str] = []
    criteria_results: list[CriterionResult] = Field(min_length=1)
    known_limits: list[str] = []


class WorkNotes(_Strict):
    """What a role records about its own task. It becomes that role's work log and personal memory."""

    did: str = Field("", max_length=300)
    decided: list[str] = Field([], max_length=5)
    promised: list[str] = Field([], max_length=5)
    lesson: str = Field("", max_length=500)
    team_lesson: str = Field("", max_length=500)


class Retrospective(_Strict):
    lessons: list[str] = []
    procedures: list[str] = []
    skill_proposals: list[str] = []


PAYLOAD_TYPES: dict[str, type[BaseModel]] = {
    model.__name__: model
    for model in (
        GoalCharter,
        Plan,
        TaskPlan,
        TaskBrief,
        SkillResolution,
        TaskReport,
        CodeChange,
        PublicContent,
        ResearchReport,
        ReviewVerdict,
        Handoff,
        Escalation,
        EscalationResolution,
        FinalDeliverable,
        Retrospective,
        WorkNotes,
    )
}


class Artifact(_Strict):
    artifact_id: str
    type: str
    schema_version: str = SCHEMA_VERSION
    run_id: str
    task_id: str | None = None
    produced_by: str
    attested_by: Attestation = "runtime"
    language: str | None = None
    parents: list[str] = []
    flags: Flags = Flags()
    created_at: str | None = None
    payload: dict[str, Any]


class ArtifactError(ValueError):
    """A payload failed its schema. The message is safe to show to the model."""


def parse_payload(type_name: str, payload: Any) -> BaseModel:
    model = PAYLOAD_TYPES.get(type_name)
    if model is None:
        raise ArtifactError(f"unknown artifact type '{type_name}'")
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(p) for p in item['loc']) or '(root)'}: {item['msg']}" for item in error.errors()
        )
        raise ArtifactError(f"{type_name} is invalid: {details}") from error


def make_artifact(
    type_name: str,
    payload: Any,
    *,
    run_id: str,
    produced_by: str,
    task_id: str | None = None,
    attested_by: Attestation = "runtime",
    language: str | None = None,
    parents: list[str] | None = None,
    flags: Flags | None = None,
    created_at: str | None = None,
) -> Artifact:
    """Validate a payload and wrap it. The ID is a hash of everything except created_at."""
    parsed = parse_payload(type_name, payload).model_dump(mode="json")
    used_flags = flags or Flags()
    identity = {
        "type": type_name,
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "task_id": task_id,
        "produced_by": produced_by,
        "attested_by": attested_by,
        "language": language,
        "parents": parents or [],
        "flags": used_flags.model_dump(),
        "payload": parsed,
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return Artifact(
        artifact_id=f"art-{digest[:24]}",
        type=type_name,
        run_id=run_id,
        task_id=task_id,
        produced_by=produced_by,
        attested_by=attested_by,
        language=language,
        parents=parents or [],
        flags=used_flags,
        created_at=created_at,
        payload=parsed,
    )


def artifact_schemas() -> dict[str, dict[str, Any]]:
    """Generated JSON Schemas, keyed by file name."""
    schemas = {"Artifact.schema.json": Artifact.model_json_schema()}
    for name, model in PAYLOAD_TYPES.items():
        schemas[f"{name}.schema.json"] = model.model_json_schema()
    return schemas
