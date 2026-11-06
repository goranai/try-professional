"""Run state, events, and actions. All plain data, so a run can be saved and resumed."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..artifacts import Artifact, Flags


class RunPhase(str, Enum):
    INTAKE = "intake"
    PLANNING = "planning"
    DISPATCH = "dispatch"
    SKILL_RESOLUTION = "skill_resolution"
    EXECUTION = "execution"
    REVIEW = "review"
    INTEGRATION = "integration"
    RETROSPECTIVE = "retrospective"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RESOLVING_SKILLS = "resolving_skills"
    READY = "ready"
    RUNNING = "running"
    IN_REVIEW = "in_review"
    WAITING = "waiting"
    ESCALATED = "escalated"
    APPROVED = "approved"
    FAILED = "failed"


class PackageStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    ACTIVE = "active"
    APPROVED = "approved"
    FAILED = "failed"


class Purpose(str, Enum):
    INTAKE = "intake"
    PLAN = "plan"
    DISPATCH = "dispatch"
    EXECUTE = "execute"
    REVIEW = "review"
    ESCALATION = "escalation"
    INTEGRATE = "integrate"
    RETROSPECTIVE = "retrospective"


FINAL_TASK_ID = "__final__"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Invocation(_Model):
    """One open request for a role to produce one artifact."""

    id: str
    role: str
    purpose: Purpose
    output_type: str
    task_id: str | None = None
    work_package_id: str | None = None
    gate: str | None = None
    subject_artifact_id: str | None = None
    origin_invocation_id: str | None = None
    attempts: int = 0
    escalation_count: int = 0
    feedback: list[str] = []
    context_refs: list[str] = []


class TaskState(_Model):
    task_id: str
    work_package_id: str | None
    title: str
    owner_role: str
    reviewer_role: str
    definition_of_done: list[str] = []
    depends_on: list[str] = []
    output_type: str
    flags: Flags = Flags()
    status: TaskStatus = TaskStatus.PENDING
    brief_artifact_id: str | None = None
    result_artifact_id: str | None = None
    pending_gates: list[str] = []
    passed_gates: list[str] = []
    rework_count: int = 0
    escalation_count: int = 0
    memory_context: list[str] = []
    skill_artifact_id: str | None = None
    feedback: list[str] = []
    notes_artifact_id: str | None = None


class PackageState(_Model):
    id: str
    title: str
    owner_department: str
    depends_on: list[str] = []
    acceptance: list[str] = []
    flags: Flags = Flags()
    status: PackageStatus = PackageStatus.PENDING
    resumes_invocation_id: str | None = None
    origin: Literal["plan", "handoff", "amendment"] = "plan"
    token_budget: int = 0


class Counters(_Model):
    """Failed or redone steps, counted the same way for every run."""

    invalid_outputs: int = 0
    rework_loops: int = 0
    failed_invocations: int = 0
    escalations: int = 0
    handoffs: int = 0
    violations: int = 0
    package_overruns: int = 0


class Usage(_Model):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    calls: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class HumanRequest(_Model):
    id: str
    question: str
    options: list[str]
    kind: Literal["escalation", "gate_unavailable", "budget", "stalled"]
    task_id: str | None = None
    gate: str | None = None
    invocation_id: str | None = None


class RunState(_Model):
    run_id: str
    goal: str
    phase: RunPhase = RunPhase.INTAKE
    seq: int = 0
    user_language: str | None = None
    charter_artifact_id: str | None = None
    plan_artifact_id: str | None = None
    final_artifact_id: str | None = None
    retrospective_artifact_id: str | None = None
    activated: dict[str, str] = {}
    packages: dict[str, PackageState] = {}
    tasks: dict[str, TaskState] = {}
    invocations: dict[str, Invocation] = {}
    suspended: dict[str, Invocation] = {}
    artifacts: dict[str, Artifact] = {}
    human_requests: dict[str, HumanRequest] = {}
    waivers: list[dict[str, str]] = []
    counters: Counters = Counters()
    usage: Usage = Usage()
    usage_by_role: dict[str, Usage] = {}
    usage_by_purpose: dict[str, Usage] = {}
    package_usage: dict[str, int] = {}
    budget_override: int | None = None
    failure_reason: str | None = None
    size: Literal["small", "large"] = "large"
    cancelled: bool = False


# ---- Events: everything that can happen to a run ----------------------------


class RunStarted(_Model):
    kind: Literal["run_started"] = "run_started"


class Submission(_Model):
    """What an agent hands in. The orchestrator wraps it in an Artifact."""

    kind: Literal["artifact_submitted"] = "artifact_submitted"
    invocation_id: str
    actor: str
    attested_by: Literal["runtime", "claimed"] = "runtime"
    payload: dict[str, Any]
    flags: Flags = Flags()
    language: str | None = None
    created_at: str | None = None
    notes: dict[str, Any] | None = None


class EscalationRaised(_Model):
    kind: Literal["escalation_raised"] = "escalation_raised"
    invocation_id: str
    actor: str
    attested_by: Literal["runtime", "claimed"] = "runtime"
    payload: dict[str, Any]


class HandoffRaised(_Model):
    kind: Literal["handoff_raised"] = "handoff_raised"
    invocation_id: str
    actor: str
    attested_by: Literal["runtime", "claimed"] = "runtime"
    payload: dict[str, Any]


class InvocationFailed(_Model):
    kind: Literal["invocation_failed"] = "invocation_failed"
    invocation_id: str
    reason: str
    retryable: bool = True


class ContextResolved(_Model):
    kind: Literal["context_resolved"] = "context_resolved"
    task_id: str
    query: str
    memory_context: list[str] = []
    skill_candidates: list[dict[str, Any]] = []


class UsageRecorded(_Model):
    kind: Literal["usage_recorded"] = "usage_recorded"
    invocation_id: str | None = None
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    role: str | None = None


class HumanDecided(_Model):
    kind: Literal["human_decided"] = "human_decided"
    request_id: str
    decision: str
    note: str = ""
    decided_by: str = "human"
    new_token_budget: int | None = None


class RunCancelled(_Model):
    """A person stopped this command. Nothing more is spent on it."""

    kind: Literal["run_cancelled"] = "run_cancelled"
    reason: str = "Cancelled by a person"


Event = Annotated[
    RunStarted
    | Submission
    | EscalationRaised
    | HandoffRaised
    | InvocationFailed
    | ContextResolved
    | UsageRecorded
    | HumanDecided
    | RunCancelled,
    Field(discriminator="kind"),
]


# ---- Actions: what the outside world must now do -----------------------------


class InvokeRole(_Model):
    kind: Literal["invoke_role"] = "invoke_role"
    invocation_id: str
    role: str
    purpose: Purpose
    output_type: str
    task_id: str | None = None
    gate: str | None = None


class ResolveContext(_Model):
    kind: Literal["resolve_context"] = "resolve_context"
    task_id: str
    role: str
    query: str


class DepartmentActivated(_Model):
    kind: Literal["department_activated"] = "department_activated"
    department: str
    reason: str


class AskHuman(_Model):
    kind: Literal["ask_human"] = "ask_human"
    request_id: str
    question: str
    options: list[str]


class Finish(_Model):
    kind: Literal["finish"] = "finish"
    status: Literal["done", "failed"]
    final_artifact_id: str | None = None
    reason: str | None = None


Action = InvokeRole | ResolveContext | DepartmentActivated | AskHuman | Finish
