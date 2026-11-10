"""What a role hands in, checked by kind: a charter, a plan, a task plan, work, a verdict, or a final report."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..artifacts import (
    Artifact,
    ArtifactError,
    EscalationResolution,
    FinalDeliverable,
    GoalCharter,
    Plan,
    Retrospective,
    ReviewVerdict,
    TaskPlan,
    make_artifact,
    parse_payload,
    settle_notes,
    settle_payload,
)
from ..gates import deterministic_flags, triggered_gates
from .base import QA_GATE, _check_actor, _not_done, _unreported
from .budget import _token_limit
from .decisions import _on_resolution
from .planning import (
    _add_package,
    _add_tasks,
    _route_problem,
    _settled_route,
    _start_small,
    _validate_plan,
    _validate_task_plan,
    named_elsewhere,
    org_change_problem,
)
from .review import _verdict_problem
from .state import (
    FINAL_TASK_ID,
    Finish,
    Invocation,
    PackageStatus,
    Purpose,
    RunPhase,
    Submission,
)

if TYPE_CHECKING:
    from .step import _Step


def _on_submission(step: _Step, event: Submission) -> None:
    state, org = step.state, step.org
    invocation = _check_actor(state, event.invocation_id, event.actor)
    submitted, submitted_notes = event.payload, event.notes
    if invocation.purpose == Purpose.EXECUTE:
        submitted, submitted_notes = settle_notes(submitted, submitted_notes)
    submitted = settle_payload(invocation.output_type, submitted)
    if invocation.purpose == Purpose.REVIEW and isinstance(submitted, dict):
        # Which work and which review this is, code already knows. A real reviewer mistyped the 24-character id six
        # times, and the run stopped over it.
        submitted = {**submitted, "subject_artifact_id": invocation.subject_artifact_id, "gate": invocation.gate}
    try:
        parsed = parse_payload(invocation.output_type, submitted)
    except ArtifactError as error:
        step.reject(invocation, str(error))
        return
    if event.missing_files:
        step.reject(invocation, f"These files aren't in the workspace: {', '.join(event.missing_files)}")
        return

    task = state.tasks.get(invocation.task_id) if invocation.task_id else None
    payload = parsed.model_dump(mode="json")
    declared = event.flags
    if task is not None:
        declared = declared.union(task.flags)
    flags = declared.union(deterministic_flags(invocation.output_type, payload))

    def artifact() -> Artifact:
        return step.store(
            make_artifact(
                invocation.output_type,
                payload,
                run_id=state.run_id,
                produced_by=event.actor,
                task_id=invocation.task_id,
                attested_by=event.attested_by,
                language=event.language,
                parents=list(invocation.context_refs),
                flags=flags,
                created_at=event.created_at,
            )
        )

    def close() -> None:
        state.invocations.pop(invocation.id, None)

    if invocation.purpose == Purpose.INTAKE:
        assert isinstance(parsed, GoalCharter)
        problem = org_change_problem(parsed.org_changes, org)
        if problem:
            step.reject(invocation, problem)
            return
        if parsed.size == "small":
            parsed = _settled_route(parsed)
            problem = _route_problem(parsed, org)
            if problem:
                step.reject(invocation, problem)
                return
            others = named_elsewhere(parsed, org)
            if others:
                # Not refused: the command is sound, and planning is the step that can bring in another department.
                needs = f"Its criteria need {', '.join(others)}, outside {parsed.route_department or parsed.route_role}"
                parsed = parsed.model_copy(
                    update={
                        "size": "large",
                        "route_department": "",
                        "route_role": "",
                        "constraints": [*parsed.constraints, f"{needs}: plan work for each."],
                    }
                )
                payload = parsed.model_dump(mode="json")
        stored = artifact()
        state.charter_artifact_id = stored.artifact_id
        state.user_language = parsed.user_language
        close()
        if parsed.size == "small":
            _start_small(step, parsed, stored)
        else:
            step.invoke(org.root_role, Purpose.PLAN, "Plan", context_refs=[stored.artifact_id])

    elif invocation.purpose == Purpose.PLAN:
        assert isinstance(parsed, Plan)
        problem = _validate_plan(parsed, org, _token_limit(state, org))
        if problem:
            step.reject(invocation, problem)
            return
        stored = artifact()
        state.plan_artifact_id = stored.artifact_id
        close()
        for activation in parsed.activated_departments:
            step.activate(activation.id, activation.reason)
        for package in parsed.work_packages:
            _add_package(step, package, "plan")

    elif invocation.purpose == Purpose.DISPATCH:
        assert isinstance(parsed, TaskPlan)
        owning = state.packages[invocation.work_package_id or ""]
        problem = _validate_task_plan(parsed, owning, org)
        if problem:
            step.reject(invocation, problem)
            return
        stored = artifact()
        close()
        owning.status = PackageStatus.ACTIVE
        _add_tasks(step, owning, parsed.tasks, invocation.role, stored.artifact_id)

    elif invocation.purpose == Purpose.EXECUTE:
        assert task is not None
        missing = _unreported(task.definition_of_done, payload.get("done_criteria_met"))
        if missing:
            step.reject(
                invocation,
                "done_criteria_met must report on every item in the definition of done, word for word. "
                f"Missing: {'; '.join(missing)}",
            )
            return
        notes = None
        if submitted_notes is not None:
            try:
                notes = parse_payload("WorkNotes", submitted_notes)
            except ArtifactError as error:
                step.reject(invocation, str(error))
                return
        stored = artifact()
        close()
        if notes is not None:
            task.notes_artifact_id = step.store(
                make_artifact(
                    "WorkNotes",
                    notes.model_dump(mode="json"),
                    run_id=state.run_id,
                    produced_by=event.actor,
                    task_id=task.task_id,
                    attested_by=event.attested_by,
                    parents=[stored.artifact_id],
                )
            ).artifact_id
        task.result_artifact_id = stored.artifact_id
        task.flags = flags
        task.pending_gates = [QA_GATE, *triggered_gates(org, flags)]
        task.passed_gates = []
        step.next_gate(task)

    elif invocation.purpose == Purpose.REVIEW:
        assert isinstance(parsed, ReviewVerdict) and task is not None
        if parsed.subject_artifact_id != invocation.subject_artifact_id:
            step.reject(invocation, f"subject_artifact_id must be '{invocation.subject_artifact_id}'")
            return
        if parsed.gate != invocation.gate:
            step.reject(invocation, f"gate must be '{invocation.gate}'")
            return
        problem = _verdict_problem(parsed)
        if problem:
            step.reject(invocation, problem)
            return
        artifact()
        close()
        if parsed.decision == "approve":
            task.pending_gates.remove(invocation.gate or "")
            task.passed_gates.append(invocation.gate or "")
            step.next_gate(task)
        else:
            summary = "; ".join(i.description for i in parsed.issues) or "No reason given"
            if parsed.decision == "changes_requested":
                step.request_rework(task, f"{invocation.gate} requested changes: {summary}")
            else:
                placeholder = Invocation(
                    id=step.next_id("inv"),
                    role=task.owner_role,
                    purpose=Purpose.INTEGRATE if task.task_id == FINAL_TASK_ID else Purpose.EXECUTE,
                    output_type=task.output_type,
                    task_id=task.task_id,
                    feedback=[f"{invocation.gate} blocked this: {summary}"],
                )
                state.invocations[placeholder.id] = placeholder
                step.escalate_invocation(placeholder, f"{invocation.gate} blocked {task.task_id}: {summary}")

    elif invocation.purpose == Purpose.ESCALATION:
        assert isinstance(parsed, EscalationResolution)
        _on_resolution(step, invocation, parsed, artifact, close)

    elif invocation.purpose == Purpose.INTEGRATE:
        assert isinstance(parsed, FinalDeliverable) and task is not None
        charter = state.artifacts[state.charter_artifact_id or ""]
        wanted = charter.payload["success_criteria"]
        reported = {r.criterion for r in parsed.criteria_results}
        missing = [c for c in wanted if c not in reported]
        if missing:
            step.reject(
                invocation, "criteria_results must cover every success criterion. Missing: " + "; ".join(missing)
            )
            return
        for task_state in state.tasks.values():
            flags = flags.union(task_state.flags)
        flags = flags.union(deterministic_flags("GoalCharter", charter.payload))
        # Code adds these, so a final report can't leave them out.
        stated = list(payload.get("known_limits") or [])
        payload["known_limits"] = stated + [line for line in _not_done(state) if line not in stated]
        stored = step.store(
            make_artifact(
                "FinalDeliverable",
                payload,
                run_id=state.run_id,
                produced_by=event.actor,
                task_id=FINAL_TASK_ID,
                attested_by=event.attested_by,
                language=event.language or state.user_language,
                parents=list(invocation.context_refs),
                flags=flags,
                created_at=event.created_at,
            )
        )
        close()
        state.final_artifact_id = stored.artifact_id
        task.result_artifact_id = stored.artifact_id
        task.flags = flags
        task.pending_gates = triggered_gates(org, flags)
        step.next_gate(task)

    elif invocation.purpose == Purpose.RETROSPECTIVE:
        problem = org_change_problem(parsed.org_changes, org) if isinstance(parsed, Retrospective) else None
        if problem:
            step.reject(invocation, problem)
            return
        stored = artifact()
        close()
        state.retrospective_artifact_id = stored.artifact_id
        state.human_requests.clear()
        state.phase = RunPhase.DONE
        step.actions.append(Finish(status="done", final_artifact_id=state.final_artifact_id))
