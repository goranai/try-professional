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
    # Spaces alone can't pass as a summary or a body.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


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
    unverified: bool = Field(False, description="True if this claim has no source a person could check")

    @model_validator(mode="after")
    def _says_where_it_came_from(self) -> Claim:
        if not self.source and not self.unverified:
            raise ValueError("A claim needs a source. If it has none, set unverified to true.")
        return self


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
    # What the other department has that this one doesn't: a role, a skill, an access. Handing work across for no
    # reason is the slowest thing a company does.
    because: str = NonEmpty
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


_NOTE_TEXT_LIMITS = {"did": 300, "lesson": 500, "team_lesson": 500}
_NOTE_LIST_LIMIT = 5


class WorkNotes(_Strict):
    """What a role records about its own task. It becomes that role's work log and personal memory."""

    did: str = Field("", max_length=_NOTE_TEXT_LIMITS["did"])
    decided: list[str] = Field([], max_length=_NOTE_LIST_LIMIT)
    promised: list[str] = Field([], max_length=_NOTE_LIST_LIMIT)
    lesson: str = Field("", max_length=_NOTE_TEXT_LIMITS["lesson"])
    team_lesson: str = Field("", max_length=_NOTE_TEXT_LIMITS["team_lesson"])


def settle_notes(payload: Any, notes: Any) -> tuple[Any, Any]:
    """A role's notes are bookkeeping, so a slip in them can't cost it the work it did.

    In a real run, notes in the wrong place or 40 characters too long threw away a finished deliverable and charged for
    it again, until the command hit its token limit. Notes in the payload move to where they belong, and long ones are
    cut, so what reaches memory stays just as small. Anything else wrong with them is still refused.
    """
    if notes is None and isinstance(payload, dict) and "notes" in payload:
        notes = payload["notes"]
        payload = {key: value for key, value in payload.items() if key != "notes"}
    if not isinstance(notes, dict):
        return payload, notes
    fitted = dict(notes)
    # Other names for the same notes. A real role wrote "did_not_do", and lost its finished work over it.
    for other, name in _NOTE_ALIASES.items():
        value = fitted.pop(other, None)
        if isinstance(value, list):
            value = "; ".join(str(item) for item in value)
        if isinstance(value, str) and value.strip():
            if name == "did":
                value = f"Not done: {value}" if other in ("did_not_do", "not_done") else value
                fitted["did"] = f"{fitted.get('did', '')} {value}".strip()
            elif name in ("decided", "promised"):
                fitted[name] = [*(fitted.get(name) or []), value]
            elif not fitted.get(name):
                fitted[name] = value
    fitted = {key: value for key, value in fitted.items() if key in WorkNotes.model_fields}
    for name, limit in _NOTE_TEXT_LIMITS.items():
        if isinstance(fitted.get(name), str):
            fitted[name] = fitted[name][:limit]
    for name in ("decided", "promised"):
        if isinstance(fitted.get(name), list):
            fitted[name] = fitted[name][:_NOTE_LIST_LIMIT]
    return payload, fitted


_NOTE_ALIASES = {
    "did_not_do": "did",
    "not_done": "did",
    "done": "did",
    "summary": "did",
    "decisions": "decided",
    "promises": "promised",
    "follow_ups": "promised",
    "lessons": "lesson",
    "learned": "lesson",
    "team_lessons": "team_lesson",
}


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


# What models often call a field instead. Only names that can't mean anything else here.
_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "Claim": {"claim": ("text", "statement", "content"), "source": ("sources", "url", "link", "citation")},
    "CriterionResult": {
        "criterion": ("text", "item", "name", "criteria"),
        "met": (
            "satisfied",
            "passed",
            "done",
            "is_met",
            "achieved",
            "complete",
            "completed",
            "fulfilled",
            "status",
            "result",
        ),
    },
    "DoneCriterion": {
        "criterion": ("text", "item", "name", "criteria"),
        "met": (
            "satisfied",
            "passed",
            "done",
            "is_met",
            "achieved",
            "complete",
            "completed",
            "fulfilled",
            "status",
            "result",
        ),
    },
    "ReviewIssue": {
        "description": (
            "issue",
            "problem",
            "text",
            "finding",
            "detail",
            "summary",
            "message",
            "comment",
            "reason",
            "explanation",
            "title",
            "note",
        ),
    },
    "TestRun": {
        "command": ("cmd", "test", "check", "name", "how", "step"),
        "result": ("output", "outcome", "status", "evidence", "passed"),
    },
    "Handoff": {"because": ("reason", "why", "justification", "rationale", "needs")},
    "DepartmentActivation": {"id": ("department", "department_id", "dept", "name"), "reason": ("why", "rationale")},
    "WorkPackage": {
        "id": ("package_id", "work_package_id", "key"),
        "owner_department": ("department", "owner", "owner_dept", "department_id"),
        "acceptance": (
            "acceptance_criteria",
            "criteria",
            "done_when",
            "success_criteria",
            "acceptance_tests",
            "definition_of_done",
            "done_criteria",
            "checks",
        ),
    },
}
# How a model says a criterion was met, or wasn't. Anything else stays as it is, and is refused.
_SAID_MET = {"met", "yes", "true", "done", "pass", "passed", "complete", "completed", "achieved", "satisfied", "ok"}
_SAID_NOT_MET = {"not met", "unmet", "no", "false", "fail", "failed", "partial", "partly met", "pending", "not done"}
# Extra detail on a review issue that's worth keeping, folded into its description.
_ISSUE_DETAIL = ("where", "location", "fix", "suggestion", "recommendation")


def settle_payload(type_name: str, payload: Any) -> Any:
    """Forgive how cheap models word a payload, and never what it says.

    In real runs, every hand-in was refused over field names: "text" for "claim", "issue" and "fix" for
    "description", or an extra "status". Known alternative names are mapped, unknown fields in nested items are
    dropped, and a claim with no source is marked unverified, which is what a person is then told. The strict check
    still runs afterwards, on everything that matters: missing content, wrong values, and unmet rules.
    """
    model = PAYLOAD_TYPES.get(type_name)
    if isinstance(payload, str):
        # The whole payload sent as JSON text, which one model did three times in a row.
        try:
            decoded = json.loads(payload)
        except ValueError:
            return payload
        payload = decoded if isinstance(decoded, dict) else payload
    if model is None or not isinstance(payload, dict):
        return payload
    return _settle(model, payload, top=True)


def _settle(model: type[BaseModel], data: dict[str, Any], *, top: bool = False) -> dict[str, Any]:
    out = dict(data)
    for field, names in _ALIASES.get(model.__name__, {}).items():
        if field not in out:
            found = next((name for name in names if name in out), None)
            if found is not None:
                value = out.pop(found)
                out[field] = value[0] if field == "source" and isinstance(value, list) and value else value
    if model.__name__ == "ReviewIssue":
        extra = [f"{name}: {out[name]}" for name in _ISSUE_DETAIL if isinstance(out.get(name), str) and out[name]]
        if extra and isinstance(out.get("description"), str):
            out["description"] = f"{out['description']} ({'; '.join(extra)})"
        if isinstance(out.get("severity"), str):
            severity = out["severity"].strip().lower()
            out["severity"] = {"critical": "blocker", "high": "major", "medium": "major", "low": "minor"}.get(
                severity, severity
            )
    if model.__name__ == "Claim":
        if out.get("verified") is False:
            out["unverified"] = True
        if not out.get("source") and not out.get("unverified"):
            out["unverified"] = True
    if model.__name__ == "WorkPackage" and not out.get("id") and isinstance(out.get("title"), str):
        # A name for it, from its own title. Nothing it says changes.
        out["id"] = re.sub(r"[^a-z0-9]+", "-", out["title"].lower()).strip("-")[:40] or out.get("id")
    if model.__name__ in ("CriterionResult", "DoneCriterion") and isinstance(out.get("met"), str):
        said = out["met"].strip().lower().replace("_", " ")
        if said in _SAID_MET:
            out["met"] = True
        elif said in _SAID_NOT_MET:
            out["met"] = False
    if model.__name__ == "TestRun" and isinstance(out.get("result"), bool):
        out["result"] = "passed" if out["result"] else "failed"
    fields = model.model_fields
    if top:
        # A role's own notes, or the language, put inside the payload by mistake. They're bookkeeping, and an
        # executing role's notes have already been moved to where they belong. Flags are never dropped: they decide
        # which reviews run.
        for wrapper in ("notes", "language"):
            if wrapper not in fields:
                out.pop(wrapper, None)
    for name, info in fields.items():
        # One item where a list of text belongs: "acceptance": "It works", for ["It works"].
        if isinstance(out.get(name), str) and _is_text_list(info.annotation):
            out[name] = [out[name]]
    if not top:
        # Nested items lose fields they don't have. Top-level extras are still refused, where they mean a wrong type.
        out = {key: value for key, value in out.items() if key in fields}
    for name, info in fields.items():
        inner = _item_model(info.annotation)
        if inner is None or name not in out:
            continue
        value = out[name]
        if isinstance(value, list):
            out[name] = [_settle(inner, item) if isinstance(item, dict) else item for item in value]
        elif isinstance(value, dict):
            out[name] = _settle(inner, value)
    return out


def _is_text_list(annotation: Any) -> bool:
    import typing

    return typing.get_origin(annotation) is list and typing.get_args(annotation) == (str,)


def _item_model(annotation: Any) -> type[BaseModel] | None:
    """The model a field holds, alone or in a list, if it holds one."""
    import typing

    candidates = [annotation, *typing.get_args(annotation)]
    for candidate in candidates:
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
    return None


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
