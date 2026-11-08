"""Budgets and progress: token limits, stuck runs, runaway assignments, and runs with nothing left to do."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...org import Org
from .base import TERMINAL_PHASES
from .state import (
    ESTIMATED_BY_SERVER,
    Invocation,
    Purpose,
    RunPhase,
    RunState,
    TaskStatus,
    Usage,
    UsageRecorded,
)

if TYPE_CHECKING:
    from .step import _Step


def _note_package_usage(step: _Step, event: UsageRecorded) -> None:
    """A package's budget is the CEO's estimate. Going over it is counted. Only the run's limit stops a run."""
    state = step.state
    invocation = state.invocations.get(event.invocation_id or "")
    if invocation is None:
        return
    task = state.tasks.get(invocation.task_id or "")
    package_id = invocation.work_package_id or (task.work_package_id if task else None)
    package = state.packages.get(package_id or "")
    if package is None:
        return
    before = state.package_usage.get(package.id, 0)
    after = before + event.input_tokens + event.output_tokens
    state.package_usage[package.id] = after
    if package.token_budget and before <= package.token_budget < after:
        state.counters.package_overruns += 1


def _token_limit(state: RunState, org: Org) -> int:
    """A person's own decision wins. Otherwise every command has a limit, and a small one has a smaller one."""
    if state.budget_override:
        return state.budget_override
    return _own_limit(state, org)


def _own_limit(state: RunState, org: Org) -> int:
    """The command's own limit, before any budget a person added."""
    pipeline = org.pipeline
    if state.usage.estimated:
        # Over MCP: corrected estimates, with their own limits.
        return pipeline.mcp_max_tokens_per_small_command if state.size == "small" else pipeline.mcp_max_tokens_per_run
    return pipeline.max_tokens_per_small_command if state.size == "small" else pipeline.max_tokens_per_run


# Model calls with nothing moving before a run counts as stuck. Two full attempts at a step, on the API runner.
STUCK_CALLS = 24


# Hand-ins refused, with nothing moving. A few tries at the same thing, and it's a loop.
STUCK_REFUSALS = 3


# Tokens spent with nothing moving, as a share of the command's own limit: a whole command's worth. One slow but real
# step on a cheap model was measured at most of a small command's limit, so less would stop real work. Budget a
# person adds doesn't raise it, so resuming a loop doesn't buy more of the same loop.
STUCK_TOKEN_SHARE = 1.0


# At the token limit, this many calls with nothing moving means stuck, not merely expensive.
STUCK_AT_LIMIT = 8


def _progress_mark(state: RunState) -> str:
    """What changes when a run gets anywhere: work accepted, approved, or sent back, or a task added or settled.

    Not a manager's answer to an escalation, or the same work assigned again: in a real run, those made a loop look
    like progress for 13 calls.
    """
    work = sum(artifact.type != "EscalationResolution" for artifact in state.artifacts.values())
    settled = sum(task.status in (TaskStatus.APPROVED, TaskStatus.FAILED) for task in state.tasks.values())
    approved = sum(task.status == TaskStatus.APPROVED for task in state.tasks.values())
    rework = state.counters.rework_loops
    return f"{work}:{approved}:{settled}:{len(state.tasks)}:{rework}:{state.phase.value}"


def _ask_stuck(step: _Step, invocation: Invocation | None, calls: int) -> None:
    """Say which role, doing what, and what keeps failing, and offer choices that don't just add budget."""
    state = step.state
    spent = f"{calls} model calls and {state.usage.total - state.tokens_at_progress} tokens"
    refused = state.counters.invalid_outputs - state.refusals_at_progress
    if refused:
        spent += f", with {refused} hand-ins refused,"
    if invocation is None:
        step.ask_human(
            f"This command looks stuck: {spent} with nothing accepted, approved, or sent back. Retry, or cancel?",
            ["retry", "cancel"],
            "stuck",
        )
        return
    failing = invocation.feedback[-1] if invocation.feedback else "nothing it handed in was accepted"
    can_waive = invocation.purpose == Purpose.REVIEW and invocation.gate is not None
    waive = (
        f" Waive the {invocation.gate} review of this task, with your name and a reason (--by, --note)."
        if can_waive
        else ""
    )
    step.ask_human(
        f"This command looks stuck. {invocation.role} has used {spent} on its "
        f"{invocation.purpose.value} step with nothing accepted, approved, or sent back. "
        f"What keeps failing: {failing[:300]} "
        f"Retry it, ideally with another model for {invocation.role} (--model).{waive} Or cancel the command.",
        ["retry", "waive", "cancel"] if can_waive else ["retry", "cancel"],
        "stuck",
        task_id=invocation.task_id,
        gate=invocation.gate,
        invocation_id=invocation.id,
    )


def _check_progress(step: _Step, event: UsageRecorded) -> bool:
    """True if this call left the run stuck. Tokens rising while nothing moves is a loop, not work."""
    state = step.state
    mark = _progress_mark(state)
    if mark != state.progress_mark:
        state.progress_mark = mark
        state.calls_since_progress = 0
        state.tokens_at_progress = state.usage.total
        state.refusals_at_progress = state.counters.invalid_outputs
        return False
    state.calls_since_progress += 1
    if state.phase in TERMINAL_PHASES or state.phase == RunPhase.BLOCKED:
        return False
    looping = (
        state.calls_since_progress >= STUCK_CALLS
        or state.counters.invalid_outputs - state.refusals_at_progress >= STUCK_REFUSALS
        or state.usage.total - state.tokens_at_progress >= _own_limit(state, step.org) * STUCK_TOKEN_SHARE
    )
    if not looping:
        return False
    _ask_stuck(step, state.invocations.get(event.invocation_id or ""), state.calls_since_progress)
    return True


def _on_usage(step: _Step, event: UsageRecorded) -> None:
    state = step.state
    state.usage.estimated = state.usage.estimated or event.model == ESTIMATED_BY_SERVER
    buckets = [state.usage]
    if event.role:
        buckets.append(state.usage_by_role.setdefault(event.role, Usage()))
    invocation = state.invocations.get(event.invocation_id or "")
    if invocation is not None:
        buckets.append(state.usage_by_purpose.setdefault(invocation.purpose.value, Usage()))
    for bucket in buckets:
        bucket.input_tokens += event.input_tokens
        bucket.output_tokens += event.output_tokens
        bucket.cached_input_tokens += event.cached_input_tokens
        bucket.calls += 1
    _note_package_usage(step, event)
    if _check_progress(step, event):
        return
    limit = _token_limit(state, step.org)
    if state.usage.total <= limit or state.phase in TERMINAL_PHASES:
        return
    if state.phase == RunPhase.BLOCKED:
        return
    if state.calls_since_progress >= STUCK_AT_LIMIT:
        # More budget wouldn't help a loop, so this asks what to change instead.
        _ask_stuck(step, state.invocations.get(event.invocation_id or ""), state.calls_since_progress)
        return
    counters = state.counters
    how = " by the server's corrected estimate" if state.usage.estimated else ""
    step.ask_human(
        f"This command stopped at {state.usage.total} tokens{how}, "
        f"over its limit of {limit}. Nothing more is spent until you decide, and its work so far is saved. "
        f"So far: {counters.assignments} assignments, {counters.invalid_outputs} rejected outputs, "
        f"{counters.rework_loops} rounds of rework, {counters.escalations} escalations. "
        f"Resuming adds {limit // 2} tokens, unless you name an amount with --token-budget. Abandoning stops it here.",
        ["resume", "abandon"],
        "budget",
    )


def _check_runaway(step: _Step) -> None:
    """However a model behaves, a run stops handing out work at some point, and a person decides."""
    state = step.state
    if state.phase in TERMINAL_PHASES or state.phase == RunPhase.BLOCKED:
        return
    if state.counters.assignments >= state.assignment_limit:
        step.ask_human(
            f"This run has handed out {state.counters.assignments} assignments without finishing. "
            "Continue, or abandon?",
            ["resume", "abandon"],
            "runaway",
        )


def _check_stall(step: _Step) -> None:
    """If nothing can ever happen next, say so instead of hanging silently."""
    state = step.state
    if state.phase in TERMINAL_PHASES or state.phase == RunPhase.BLOCKED or state.invocations:
        return
    if any(t.status in (TaskStatus.RESOLVING_SKILLS, TaskStatus.READY) for t in state.tasks.values()):
        return
    if state.final_artifact_id and state.retrospective_artifact_id:
        return
    step.ask_human(
        "The run has no work it can start. Abandon it?",
        ["abandon"],
        "stalled",
    )
