"""The pipeline: a pure function from (state, event) to (new state, actions).

No model calls, no clock, no I/O. Everything the hierarchy promises is enforced
here, so it is testable with no API key and can't be argued away by a prompt.
"""

from __future__ import annotations

from typing import Any

from ...org import Org
from ..artifacts import (
    make_artifact,
)
from .base import (
    MAX_AMENDMENTS,
    MAX_TASKS_PER_PACKAGE,
    MAX_WORK_PACKAGES,
    QA_GATE,
    TERMINAL_PHASES,
    IllegalEvent,
)
from .budget import (
    STUCK_AT_LIMIT,
    STUCK_CALLS,
    STUCK_REFUSALS,
    STUCK_TOKEN_SHARE,
    _check_runaway,
    _check_stall,
    _on_usage,
    _token_limit,
)
from .decisions import _on_escalation, _on_failure, _on_human
from .handoffs import _on_handoff
from .state import (
    Action,
    AskHuman,
    ContextResolved,
    EscalationRaised,
    Finish,
    HandoffRaised,
    HumanDecided,
    InvocationFailed,
    InvokeRole,
    Purpose,
    ResolveContext,
    RunCancelled,
    RunPhase,
    RunStarted,
    RunState,
    Submission,
    TaskStatus,
    UsageRecorded,
)
from .step import _Step
from .submissions import _on_submission


def _on_context(step: _Step, event: ContextResolved) -> None:
    state = step.state
    task = state.tasks.get(event.task_id)
    if task is None or task.status != TaskStatus.RESOLVING_SKILLS:
        raise IllegalEvent("TASK_NOT_RESOLVING", f"task '{event.task_id}' is not resolving context")
    task.memory_context = event.memory_context
    stored = step.store(
        make_artifact(
            "SkillResolution",
            {"query": event.query, "candidates": event.skill_candidates, "selected": [], "reason": ""},
            run_id=state.run_id,
            produced_by="orchestrator",
            task_id=task.task_id,
        )
    )
    task.skill_artifact_id = stored.artifact_id
    task.status = TaskStatus.READY


def _on_cancel(step: _Step, event: RunCancelled) -> None:
    step.state.cancelled = True
    step.state.human_requests.clear()
    step.fail_run(event.reason)


def initial_state(run_id: str, goal: str) -> RunState:
    return RunState(run_id=run_id, goal=goal)


def advance(state: RunState, event: Any, org: Org) -> tuple[RunState, list[Action]]:
    """Apply one event. The input state is never modified."""
    if state.phase in TERMINAL_PHASES and not isinstance(event, UsageRecorded):
        raise IllegalEvent("RUN_FINISHED", f"run is {state.phase.value}")

    step = _Step(state.model_copy(deep=True), org)

    if isinstance(event, RunStarted):
        if step.state.seq != 0:
            raise IllegalEvent("ALREADY_STARTED", "run has already started")
        for department in org.always_active():
            step.activate(department, "always active")
        step.invoke(org.root_role, Purpose.INTAKE, "GoalCharter")
    elif isinstance(event, Submission):
        _on_submission(step, event)
    elif isinstance(event, EscalationRaised):
        _on_escalation(step, event)
    elif isinstance(event, HandoffRaised):
        _on_handoff(step, event)
    elif isinstance(event, InvocationFailed):
        _on_failure(step, event)
    elif isinstance(event, ContextResolved):
        _on_context(step, event)
    elif isinstance(event, UsageRecorded):
        _on_usage(step, event)
    elif isinstance(event, HumanDecided):
        _on_human(step, event)
    elif isinstance(event, RunCancelled):
        _on_cancel(step, event)
    else:
        raise IllegalEvent("UNKNOWN_EVENT", type(event).__name__)

    step.schedule()
    _check_runaway(step)
    _check_stall(step)
    return step.state, step.actions


def open_actions(state: RunState) -> list[Action]:
    """Everything currently waiting on the outside world. Safe to call repeatedly."""
    if state.phase == RunPhase.DONE:
        return [Finish(status="done", final_artifact_id=state.final_artifact_id)]
    if state.phase == RunPhase.FAILED:
        return [Finish(status="failed", reason=state.failure_reason)]
    if state.phase == RunPhase.BLOCKED:
        return [
            AskHuman(request_id=r.id, question=r.question, options=r.options) for r in state.human_requests.values()
        ]
    actions: list[Action] = [
        InvokeRole(
            invocation_id=i.id,
            role=i.role,
            purpose=i.purpose,
            output_type=i.output_type,
            task_id=i.task_id,
            gate=i.gate,
        )
        for i in state.invocations.values()
    ]
    actions += [
        ResolveContext(task_id=t.task_id, role=t.owner_role, query=t.title)
        for t in state.tasks.values()
        if t.status == TaskStatus.RESOLVING_SKILLS
    ]
    return actions


__all__ = ["IllegalEvent", "advance", "initial_state", "open_actions"]

# Moved to modules by concern, and still importable from here.
_REEXPORTED = (
    MAX_AMENDMENTS,
    MAX_TASKS_PER_PACKAGE,
    MAX_WORK_PACKAGES,
    QA_GATE,
    STUCK_AT_LIMIT,
    STUCK_CALLS,
    STUCK_REFUSALS,
    STUCK_TOKEN_SHARE,
    TERMINAL_PHASES,
    _token_limit,
)
