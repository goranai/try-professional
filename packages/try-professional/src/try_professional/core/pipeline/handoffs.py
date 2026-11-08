"""Handoffs: the one channel between departments, through their heads."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..artifacts import (
    ArtifactError,
    Handoff,
    WorkPackage,
    make_artifact,
    parse_payload,
)
from .base import IllegalEvent, _check_actor
from .planning import _add_package
from .state import (
    HandoffRaised,
    Purpose,
    TaskStatus,
)

if TYPE_CHECKING:
    from .step import _Step


def _on_handoff(step: _Step, event: HandoffRaised) -> None:
    state, org = step.state, step.org
    invocation = _check_actor(state, event.invocation_id, event.actor)
    if event.actor == org.root_role or invocation.purpose not in (Purpose.DISPATCH, Purpose.EXECUTE):
        raise IllegalEvent(
            "HANDOFF_NOT_ALLOWED_HERE",
            f"{event.actor} can't hand off during {invocation.purpose.value}. "
            "Handoffs are for heads doing department work. The root role plans instead.",
        )
    if not org.is_head(event.actor):
        raise IllegalEvent(
            "HANDOFF_NOT_HEAD",
            f"{event.actor} is not a department head. Escalate to your head, who can request a handoff.",
        )
    try:
        parsed = parse_payload("Handoff", event.payload)
    except ArtifactError as error:
        step.reject(invocation, str(error))
        return
    assert isinstance(parsed, Handoff)
    own_department = event.actor.split("/", 1)[0]
    if parsed.from_department != own_department:
        raise IllegalEvent("HANDOFF_WRONG_DEPARTMENT", f"{event.actor} cannot hand off for '{parsed.from_department}'")
    if parsed.to_department == own_department:
        step.reject(invocation, "A handoff must target a different department")
        return
    if parsed.to_department not in org.departments:
        step.reject(invocation, f"department '{parsed.to_department}' is not installed")
        return
    stored = step.store(
        make_artifact(
            "Handoff",
            parsed.model_dump(mode="json"),
            run_id=state.run_id,
            produced_by=event.actor,
            task_id=invocation.task_id,
            attested_by=event.attested_by,
            parents=parsed.artifact_refs,
        )
    )
    state.counters.handoffs += 1
    state.invocations.pop(invocation.id)
    state.suspended[invocation.id] = invocation
    invocation.context_refs.append(stored.artifact_id)
    if invocation.task_id and invocation.task_id in state.tasks:
        state.tasks[invocation.task_id].status = TaskStatus.WAITING
    step.activate(parsed.to_department, f"handoff from {own_department}: {parsed.request[:120]}")
    package_id = step.next_id("handoff")
    _add_package(
        step,
        WorkPackage(
            id=package_id,
            title=parsed.request,
            owner_department=parsed.to_department,
            acceptance=parsed.acceptance,
        ),
        "handoff",
        invocation.id,
    )
