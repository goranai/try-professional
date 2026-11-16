"""Run state, events, and actions. All plain data, so a run can be saved and resumed."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    NOT_NEEDED = "not_needed"


class PackageStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    ACTIVE = "active"
    APPROVED = "approved"
    FAILED = "failed"
    NOT_NEEDED = "not_needed"


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
    # What a person decided when this task's work was escalated to them, for every role on the task, its reviewers too.
    person_decisions: list[str] = []
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


# However a model behaves, a run can't keep handing out work forever. A person decides whether it goes on.
MAX_ASSIGNMENTS = 200


class Counters(_Model):
    """Failed or redone steps, counted the same way for every run."""

    invalid_outputs: int = 0
    rework_loops: int = 0
    failed_invocations: int = 0
    escalations: int = 0
    handoffs: int = 0
    violations: int = 0
    package_overruns: int = 0
    assignments: int = 0
    amendments: int = 0
    # Commands, pages, and searches a role hadn't already done in that step. Doing new things is working; repeating
    # the same call isn't.
    fresh_actions: int = 0


class Usage(_Model):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    calls: int = 0
    estimated: bool = False
    estimated_tool_input_tokens: int = 0
    estimated_tool_output_tokens: int = 0
    tool_calls: int = 0
    unknown_model_calls: int = 0
    unavailable_records: int = 0
    unverified_records: int = 0
    unverified_legacy_tokens: int = 0

    @model_validator(mode="before")
    @classmethod
    def preserve_unknown_historical_totals(cls, value: Any) -> Any:
        # Old snapshots merged model usage with MCP estimates. Without their
        # audit events, neither part can be recovered from the combined total.
        if isinstance(value, dict) and value.get("estimated") and "estimated_tool_input_tokens" not in value:
            value = dict(value)
            value["unverified_legacy_tokens"] = value.get("input_tokens", 0) + value.get("output_tokens", 0)
            value.update(input_tokens=0, output_tokens=0, cached_input_tokens=0, calls=0, unverified_records=1)
        elif (
            isinstance(value, dict)
            and "estimated_tool_input_tokens" not in value
            and (value.get("input_tokens") or value.get("output_tokens") or value.get("calls"))
        ):
            value = dict(value)
            value["unverified_records"] = value.get("unverified_records", 0) + 1
        return value

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def estimated_tool_tokens(self) -> int:
        return self.estimated_tool_input_tokens + self.estimated_tool_output_tokens

    @property
    def model_usage_status(self) -> str:
        if self.estimated or self.unavailable_records or self.unverified_records:
            return "partial" if self.total else "unavailable"
        return "exact" if self.calls or self.unknown_model_calls else "unavailable"

    def lines(self) -> list[str]:
        calls = f"{self.calls} verified model calls"
        if self.unknown_model_calls:
            calls += f"; model call count unavailable for {self.unknown_model_calls} CLI process(es)"
        return [
            f"Model tokens ({self.model_usage_status}): {self.total} "
            f"({self.input_tokens} input, {self.cached_input_tokens} of input cached, "
            f"{self.output_tokens} output); {calls}.",
            f"Tool traffic: about {self.estimated_tool_tokens} tokens "
            f"({self.estimated_tool_input_tokens} input, {self.estimated_tool_output_tokens} output), "
            "estimated by the server "
            f"from {self.tool_calls} tool calls; separate from model usage.",
            *(
                ["Model usage is incomplete or unverified; unavailable counts are not zero usage."]
                if self.model_usage_status != "exact"
                else []
            ),
            *(
                [
                    f"Historical combined usage: {self.unverified_legacy_tokens} tokens, unverified; "
                    "its model and tool portions are unavailable."
                ]
                if self.unverified_legacy_tokens
                else []
            ),
        ]


class HumanRequest(_Model):
    id: str
    question: str
    options: list[str]
    kind: Literal["escalation", "gate_unavailable", "budget", "stalled", "runaway", "stuck"]
    task_id: str | None = None
    gate: str | None = None
    invocation_id: str | None = None
    usage_is_tool_traffic: bool = False


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
    # Roles trusted at the start of the run, from the company's record, and how many QA reviews each has skipped in
    # a row since its last one. A role leaves it the moment a review asks for changes.
    trust: dict[str, int] = {}
    counters: Counters = Counters()
    usage: Usage = Usage()
    usage_by_role: dict[str, Usage] = {}
    usage_by_purpose: dict[str, Usage] = {}
    usage_by_invocation: dict[str, Usage] = {}
    package_usage: dict[str, int] = {}
    budget_override: int | None = None
    # The existing optional MCP budget resumes independently of a model budget.
    tool_budget_override: int | None = None
    assignment_limit: int = MAX_ASSIGNMENTS
    failure_reason: str | None = None
    early_closure_reason: str | None = None
    early_closed_by: str | None = None
    size: Literal["small", "large"] = "large"
    cancelled: bool = False
    # Model calls since anything moved: a new artifact, approval, rework round, or assignment. See _check_progress.
    progress_mark: str = ""
    calls_since_progress: int = 0
    # Where the run stood when something last moved: its tokens, and its refused hand-ins. See _check_progress.
    tokens_at_progress: int = 0
    refusals_at_progress: int = 0
    # Another model a person chose for a role that got stuck. An adapter that picks models reads it.
    role_models: dict[str, str] = {}


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
    # Set by the orchestrator, which can see the workspace. The pipeline itself touches no files.
    missing_files: list[str] = []


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


ESTIMATED_BY_SERVER = "estimated by server"


class UsageRecorded(_Model):
    kind: Literal["usage_recorded"] = "usage_recorded"
    invocation_id: str | None = None
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    # Keep the provider's raw count beside normalized input, so audit readers need
    # not guess the caching convention from a model name or a historical record.
    raw_input_tokens: int | None = None
    cache_creation_input_tokens: int = 0
    input_tokens_includes_cache: bool | None = None
    role: str | None = None
    purpose: str | None = None
    work_package_id: str | None = None
    usage_source: str = ""
    usage_status: Literal["exact", "unavailable", "unverified"] = "exact"
    usage_note: str = ""
    raw_input_tokens_includes_cache: bool | None = None
    # A CLI's final result counts tokens across its internal calls, not the calls
    # themselves. Keeping that unknown prevents one process being called one model call.
    calls: int | None = 1


class HumanDecided(_Model):
    kind: Literal["human_decided"] = "human_decided"
    request_id: str
    decision: str
    note: str = ""
    decided_by: str = "human"
    new_token_budget: int | None = None
    # For a stuck role: another model to try it with.
    model: str | None = None


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
