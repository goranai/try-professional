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
    task = state.tasks.get(invocation.task_id or "") if invocation else None
    package_id = event.work_package_id or (
        invocation.work_package_id or (task.work_package_id if task else None) if invocation else None
    )
    package = state.packages.get(package_id or "")
    if package is None:
        return
    before = state.package_usage.get(package.id, 0)
    inputs = event.input_tokens
    if event.input_tokens_includes_cache is False:
        inputs += event.cached_input_tokens + event.cache_creation_input_tokens
    after = before + inputs + event.output_tokens
    state.package_usage[package.id] = after
    if package.token_budget and before <= package.token_budget < after:
        state.counters.package_overruns += 1


def _token_limit(state: RunState, org: Org) -> int | None:
    """Use the person's optional model budget, otherwise the configured optional budget."""
    if state.budget_override:
        return state.budget_override
    return _own_limit(state, org)


def _own_limit(state: RunState, org: Org) -> int | None:
    """The command's own limit, before any budget a person added."""
    pipeline = org.pipeline
    return pipeline.max_tokens_per_small_command if state.size == "small" else pipeline.max_tokens_per_run


def _tool_token_limit(state: RunState, org: Org) -> int | None:
    if state.tool_budget_override:
        return state.tool_budget_override
    pipeline = org.pipeline
    return pipeline.mcp_max_tokens_per_small_command if state.size == "small" else pipeline.mcp_max_tokens_per_run


# Model calls with nothing moving before a run counts as stuck. Two full attempts at a step, on the API runner.
STUCK_CALLS = 24


# Hand-ins refused, with nothing moving. A few tries at the same thing, and it's a loop.
STUCK_REFUSALS = 3
FRESH_ACTIONS_PER_STEP = 3


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
    # Doing something new on a computer, such as another command, is work in progress. Every few count as a step, so a
    # role that keeps exploring isn't called stuck, and one that repeats itself still is.
    exploring = state.counters.fresh_actions // FRESH_ACTIONS_PER_STEP
    return f"{work}:{approved}:{settled}:{len(state.tasks)}:{rework}:{state.phase.value}:{exploring}"


def _spent_a_whole_limit(state: RunState, org: Org) -> bool:
    """With a token limit set, a whole one spent with nothing moving is a loop. With none, calls and refusals decide."""
    limit = _own_limit(state, org)
    return limit is not None and state.usage.total - state.tokens_at_progress >= limit * STUCK_TOKEN_SHARE


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
    # A call through the company's tools, counted by the server, isn't a model call: a role working in its own agent
    # (Codex, Antigravity) wrote its evidence through workspace_write, and 24 such calls into a hunt were taken for a
    # loop. Refused hand-ins still are one, wherever they come from.
    counted = event.model != ESTIMATED_BY_SERVER
    if counted:
        # An agent CLI's step doesn't say how many model calls it made, but it made at least one. Counting it as none
        # switched loop detection off for every Codex, Antigravity and Claude Code role.
        state.calls_since_progress += event.calls if event.calls is not None else 1
    if state.phase in TERMINAL_PHASES or state.phase == RunPhase.BLOCKED:
        return False
    looping = (
        (counted and state.calls_since_progress >= STUCK_CALLS)
        or state.counters.invalid_outputs - state.refusals_at_progress >= STUCK_REFUSALS
        or (counted and _spent_a_whole_limit(state, step.org))
    )
    if not looping:
        return False
    _ask_stuck(step, state.invocations.get(event.invocation_id or ""), state.calls_since_progress)
    return True


def _on_usage(step: _Step, event: UsageRecorded) -> None:
    state = step.state
    buckets = [state.usage]
    if event.role:
        buckets.append(state.usage_by_role.setdefault(event.role, Usage()))
    invocation = state.invocations.get(event.invocation_id or "")
    purpose = invocation.purpose.value if invocation else event.purpose
    if purpose:
        buckets.append(state.usage_by_purpose.setdefault(purpose, Usage()))
    if event.invocation_id:
        buckets.append(state.usage_by_invocation.setdefault(event.invocation_id, Usage()))
    estimated_tool = event.model == ESTIMATED_BY_SERVER
    for bucket in buckets:
        if estimated_tool:
            bucket.estimated_tool_input_tokens += event.input_tokens
            bucket.estimated_tool_output_tokens += event.output_tokens
            bucket.tool_calls += 1
            continue
        inputs = event.input_tokens
        if event.input_tokens_includes_cache is False:
            inputs += event.cached_input_tokens + event.cache_creation_input_tokens
        bucket.input_tokens += inputs
        bucket.output_tokens += event.output_tokens
        bucket.cached_input_tokens += event.cached_input_tokens
        bucket.calls += event.calls or 0
        bucket.unknown_model_calls += int(event.calls is None)
        bucket.unavailable_records += int(event.usage_status == "unavailable")
        bucket.unverified_records += int(
            event.usage_status == "unverified" or event.input_tokens_includes_cache is None
        )
    if not estimated_tool:
        _note_package_usage(step, event)
    if _check_progress(step, event):
        return
    limit = _tool_token_limit(state, step.org) if estimated_tool else _token_limit(state, step.org)
    total = state.usage.estimated_tool_tokens if estimated_tool else state.usage.total
    if limit is None or total <= limit or state.phase in TERMINAL_PHASES:
        return
    if state.phase == RunPhase.BLOCKED:
        return
    if state.calls_since_progress >= STUCK_AT_LIMIT:
        # More budget wouldn't help a loop, so this asks what to change instead.
        _ask_stuck(step, state.invocations.get(event.invocation_id or ""), state.calls_since_progress)
        return
    counters = state.counters
    how = " of tool traffic, estimated by the server" if estimated_tool else " of model usage"
    step.ask_human(
        f"This command stopped at {total} tokens{how}, "
        f"over its limit of {limit}. Nothing more is spent until you decide, and its work so far is saved. "
        f"So far: {counters.assignments} assignments, {counters.invalid_outputs} rejected outputs, "
        f"{counters.rework_loops} rounds of rework, {counters.escalations} escalations. "
        f"Resuming adds {limit // 2} tokens, unless you name an amount with --token-budget. Abandoning stops it here.",
        ["resume", "abandon"],
        "budget",
        usage_is_tool_traffic=estimated_tool,
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
