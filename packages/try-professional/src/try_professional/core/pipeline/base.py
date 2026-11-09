"""What every part of the pipeline shares: the step being taken, its limits, and the error for an illegal event."""

from __future__ import annotations

from typing import Any

from ...org import Org
from ..artifacts import (
    Artifact,
)
from .state import (
    FINAL_TASK_ID,
    Action,
    AskHuman,
    DepartmentActivated,
    Finish,
    HumanRequest,
    Invocation,
    InvokeRole,
    Purpose,
    RunPhase,
    RunState,
    TaskStatus,
)

TERMINAL_PHASES = {RunPhase.DONE, RunPhase.FAILED}


MAX_WORK_PACKAGES = 12


MAX_TASKS_PER_PACKAGE = 12


MAX_AMENDMENTS = 5


QA_GATE = "qa"


class IllegalEvent(ValueError):
    """An event the rules forbid. It changes nothing and is recorded as a violation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class StepCore:
    """Mutable working state for one advance() call."""

    def __init__(self, state: RunState, org: Org) -> None:
        self.state = state
        self.org = org
        self.actions: list[Action] = []

    def next_id(self, prefix: str) -> str:
        self.state.seq += 1
        return f"{prefix}-{self.state.seq:04d}"

    def activate(self, department: str, reason: str) -> None:
        if department in self.state.activated:
            return
        self.state.activated[department] = reason
        self.actions.append(DepartmentActivated(department=department, reason=reason))

    def invoke(
        self,
        role: str,
        purpose: Purpose,
        output_type: str,
        *,
        task_id: str | None = None,
        work_package_id: str | None = None,
        gate: str | None = None,
        subject_artifact_id: str | None = None,
        origin_invocation_id: str | None = None,
        feedback: list[str] | None = None,
        context_refs: list[str] | None = None,
    ) -> Invocation:
        department = role.split("/", 1)[0]
        if department not in self.state.activated:
            raise AssertionError(f"invoking '{role}' in inactive department '{department}'")
        invocation = Invocation(
            id=self.next_id("inv"),
            role=role,
            purpose=purpose,
            output_type=output_type,
            task_id=task_id,
            work_package_id=work_package_id,
            gate=gate,
            subject_artifact_id=subject_artifact_id,
            origin_invocation_id=origin_invocation_id,
            feedback=feedback or [],
            context_refs=context_refs or [],
        )
        self.state.invocations[invocation.id] = invocation
        self.state.counters.assignments += 1
        self.emit(invocation)
        return invocation

    def emit(self, invocation: Invocation) -> None:
        self.actions.append(
            InvokeRole(
                invocation_id=invocation.id,
                role=invocation.role,
                purpose=invocation.purpose,
                output_type=invocation.output_type,
                task_id=invocation.task_id,
                gate=invocation.gate,
            )
        )

    def store(self, artifact: Artifact) -> Artifact:
        self.state.artifacts[artifact.artifact_id] = artifact
        return artifact

    def ask_human(
        self,
        question: str,
        options: list[str],
        kind: Any,
        *,
        task_id: str | None = None,
        gate: str | None = None,
        invocation_id: str | None = None,
    ) -> None:
        request = HumanRequest(
            id=self.next_id("hum"),
            question=question,
            options=options,
            kind=kind,
            task_id=task_id,
            gate=gate,
            invocation_id=invocation_id,
        )
        self.state.human_requests[request.id] = request
        self.state.phase = RunPhase.BLOCKED
        self.actions.append(AskHuman(request_id=request.id, question=question, options=options))

    def fail_run(self, reason: str) -> None:
        self.state.phase = RunPhase.FAILED
        self.state.failure_reason = reason
        self.state.invocations.clear()
        self.actions.append(Finish(status="failed", reason=reason))


def _check_actor(state: RunState, invocation_id: str, actor: str) -> Invocation:
    invocation = state.invocations.get(invocation_id)
    if invocation is None:
        raise IllegalEvent("NO_OPEN_INVOCATION", f"'{invocation_id}' is not open")
    if actor != invocation.role:
        raise IllegalEvent(
            "WRONG_ACTOR",
            f"{invocation_id} was assigned to {invocation.role}, not {actor}",
        )
    return invocation


def _same_words(text: str) -> str:
    return " ".join(text.split()).casefold()


# How close a reworded criterion must be to count as the same one. Small models restate criteria in their own words:
# "The plan covers five posts" for "The plan fits on one page and covers exactly five posts" is far below this.
SAME_CRITERION = 0.8


def _unreported(wanted: list[str], reported: Any) -> list[str]:
    """Which criteria an output doesn't report on, so a brief can't simply be ignored. A criterion copied with small
    changes, such as a word or punctuation, still counts; each reported one counts for one criterion at most."""
    from difflib import SequenceMatcher

    said = [_same_words(item["criterion"]) for item in reported or []]
    unused = list(said)
    missing = []
    for criterion in wanted:
        exact = _same_words(criterion)
        if exact in unused:
            unused.remove(exact)
            continue
        scored = [(SequenceMatcher(None, exact, other).ratio(), other) for other in unused]
        best = max(scored, default=(0.0, ""))
        if best[0] >= SAME_CRITERION:
            unused.remove(best[1])
        else:
            missing.append(criterion)
    return missing


def _not_done(state: RunState) -> list[str]:
    """What a person must be told, whatever the final report chose to say."""
    lines: list[str] = []
    for task in state.tasks.values():
        if task.task_id == FINAL_TASK_ID:
            continue
        if task.status == TaskStatus.FAILED:
            lines.append(f"Not done: {task.title}")
            continue
        result = state.artifacts.get(task.result_artifact_id or "")
        if result is None:
            continue
        for item in result.payload.get("done_criteria_met") or []:
            if not item.get("met"):
                lines.append(f"Not met in '{task.title}': {item.get('criterion')}")
        for item in result.payload.get("open_issues") or []:
            lines.append(f"Open in '{task.title}': {item}")
        for item in result.payload.get("findings") or []:
            if item.get("unverified"):
                lines.append(f"Unverified, check before using: {str(item.get('statement'))[:200]}")
        for item in result.payload.get("claims") or []:
            if item.get("unverified"):
                lines.append(f"Unverified, check before using: {str(item.get('claim'))[:200]}")
    return lines
