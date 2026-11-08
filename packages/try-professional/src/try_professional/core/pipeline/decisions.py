"""Decisions: what a manager makes of an escalation, a failed step, and what a person decides."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..artifacts import (
    ArtifactError,
    Escalation,
    EscalationResolution,
    make_artifact,
    parse_payload,
)
from .base import MAX_AMENDMENTS, MAX_WORK_PACKAGES, IllegalEvent, _check_actor
from .budget import _token_limit
from .planning import _add_package, _find_cycle
from .state import (
    MAX_ASSIGNMENTS,
    EscalationRaised,
    HumanDecided,
    Invocation,
    InvocationFailed,
    RunPhase,
)

if TYPE_CHECKING:
    from .step import _Step


def _on_escalation(step: _Step, event: EscalationRaised) -> None:
    state = step.state
    invocation = _check_actor(state, event.invocation_id, event.actor)
    try:
        parsed = parse_payload("Escalation", event.payload)
    except ArtifactError as error:
        step.reject(invocation, str(error))
        return
    assert isinstance(parsed, Escalation)
    step.store(
        make_artifact(
            "Escalation",
            parsed.model_dump(mode="json"),
            run_id=state.run_id,
            produced_by=event.actor,
            task_id=invocation.task_id,
            attested_by=event.attested_by,
        )
    )
    step.escalate_invocation(invocation, parsed.reason)


def _on_resolution(
    step: _Step, invocation: Invocation, parsed: EscalationResolution, artifact: Any, close: Any
) -> None:
    state, org = step.state, step.org
    origin_id = invocation.origin_invocation_id or ""
    if parsed.decision == "amend_plan":
        if invocation.role != org.root_role:
            step.reject(invocation, "Only the root role can amend the plan. Use escalate_further.")
            return
        if not parsed.added_work_packages:
            step.reject(invocation, "amend_plan needs at least one added work package")
            return
        if state.counters.amendments >= MAX_AMENDMENTS:
            step.reject(invocation, f"This run has amended its plan {MAX_AMENDMENTS} times. Use escalate_further.")
            return
        if len(parsed.added_work_packages) > MAX_WORK_PACKAGES:
            step.reject(invocation, f"An amendment can add at most {MAX_WORK_PACKAGES} work packages.")
            return
        known = set(org.departments)
        departments = f"Installed: {', '.join(sorted(known))}. To ask a person, use escalate_further."
        activating = {a.id for a in parsed.activate_departments}
        for activation in parsed.activate_departments:
            if activation.id not in known:
                step.reject(invocation, f"department '{activation.id}' is not installed. {departments}")
                return
        for package in parsed.added_work_packages:
            if package.owner_department not in known:
                step.reject(invocation, f"department '{package.owner_department}' is not installed. {departments}")
                return
            if package.owner_department not in state.activated and package.owner_department not in activating:
                step.reject(invocation, f"amendment must activate '{package.owner_department}'")
                return
            if package.id in state.packages:
                step.reject(invocation, f"package '{package.id}' already exists")
                return
        graph = {p.id: p.depends_on for p in parsed.added_work_packages}
        for package in parsed.added_work_packages:
            for dep in package.depends_on:
                if dep not in graph and dep not in state.packages:
                    step.reject(invocation, f"package '{package.id}' depends on unknown package '{dep}'")
                    return
        problem = _find_cycle(graph)
        if problem:
            step.reject(invocation, problem)
            return
        artifact()
        close()
        state.counters.amendments += 1
        for activation in parsed.activate_departments:
            step.activate(activation.id, activation.reason)
        for package in parsed.added_work_packages:
            _add_package(step, package, "amendment", origin_id)
        return

    artifact()
    close()
    if parsed.decision == "resume":
        step.resume_origin(origin_id, parsed.guidance)
    elif parsed.decision == "abandon":
        step.abandon_origin(origin_id, parsed.guidance or "abandoned on escalation")
    else:
        step.route_escalation(invocation.role, origin_id, parsed.guidance or "Escalated further")


def _on_failure(step: _Step, event: InvocationFailed) -> None:
    invocation = step.state.invocations.get(event.invocation_id)
    if invocation is None:
        raise IllegalEvent("NO_OPEN_INVOCATION", f"'{event.invocation_id}' is not open")
    step.state.counters.failed_invocations += 1
    invocation.attempts += 1
    if event.retryable and invocation.attempts <= step.org.pipeline.max_invalid_outputs:
        invocation.feedback.append(f"The previous attempt failed: {event.reason}")
        step.emit(invocation)
        return
    step.escalate_invocation(invocation, f"Could not complete: {event.reason}")


def _on_human(step: _Step, event: HumanDecided) -> None:
    state = step.state
    request = state.human_requests.pop(event.request_id, None)
    if request is None:
        raise IllegalEvent("NO_OPEN_REQUEST", f"human request '{event.request_id}' is not open")
    if event.decision not in request.options:
        state.human_requests[request.id] = request
        raise IllegalEvent("BAD_DECISION", f"'{event.decision}' is not one of {request.options}")

    if (
        request.kind == "stuck"
        and event.decision == "waive"
        and (not event.note.strip() or event.decided_by == "human")
    ):
        state.human_requests[request.id] = request
        raise IllegalEvent("WAIVER_NEEDS_A_NAME", "A waiver needs the name of who decided, and a reason.")

    if not state.human_requests:
        state.phase = RunPhase.EXECUTION

    if request.kind == "stuck":
        _on_stuck_decision(step, request, event)
        return

    if event.decision == "abandon":
        if request.invocation_id and request.invocation_id in state.suspended:
            step.abandon_origin(request.invocation_id, event.note or "abandoned by human")
        else:
            step.fail_run(event.note or f"Abandoned by human ({request.kind})")
        return

    if request.kind == "escalation" and request.invocation_id:
        step.resume_origin(request.invocation_id, event.note)
    elif request.kind == "gate_unavailable" and request.task_id and request.gate:
        task = state.tasks[request.task_id]
        step.waive(task, request.gate, event.note or "waived by human", event.decided_by)
        step.next_gate(task)
    elif request.kind == "budget":
        # With no amount, half a limit more. Otherwise the very next token would stop it again. It can stop again after.
        state.budget_override = event.new_token_budget or state.usage.total + (_token_limit(state, step.org) or 0) // 2
    elif request.kind == "runaway":
        state.assignment_limit = state.counters.assignments + MAX_ASSIGNMENTS


def _on_stuck_decision(step: _Step, request: Any, event: HumanDecided) -> None:
    state = step.state
    state.calls_since_progress = 0
    state.progress_mark = ""
    invocation = state.invocations.get(request.invocation_id or "")
    if event.decision == "cancel":
        step.fail_run(f"Stopped by {event.decided_by} after it got stuck. {event.note}".strip())
        return
    if event.decision == "waive" and invocation is not None and request.task_id and request.gate:
        # Only a review can be waived: there's no work to approve when the work itself is what's stuck.
        task = state.tasks[request.task_id]
        state.invocations.pop(invocation.id, None)
        step.waive(task, request.gate, event.note, event.decided_by)
        step.next_gate(task)
        return
    if invocation is not None:
        invocation.attempts = 0
        if event.model:
            state.role_models[invocation.role] = event.model
        invocation.feedback.append(
            "A person asked for another try"
            + (f", with {event.model}" if event.model else "")
            + ". Do what's asked, and hand it in, rather than redoing the same steps."
        )
