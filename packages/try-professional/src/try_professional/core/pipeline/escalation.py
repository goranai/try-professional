"""Escalation: output sent back, and an item climbing the hierarchy until someone can decide it."""

from __future__ import annotations

from .base import StepCore
from .state import (
    FINAL_TASK_ID,
    Invocation,
    PackageStatus,
    Purpose,
    TaskStatus,
)


class StepEscalation(StepCore):
    def reject(self, invocation: Invocation, reason: str) -> None:
        """An output was wrong. Ask again with the reason, or escalate after too many tries."""
        self.state.counters.invalid_outputs += 1
        invocation.attempts += 1
        invocation.feedback.append(reason)
        if invocation.attempts <= self.org.pipeline.max_invalid_outputs:
            self.emit(invocation)
            return
        self.escalate_invocation(invocation, f"Repeated invalid output: {reason}")

    def escalate_invocation(self, invocation: Invocation, reason: str) -> None:
        """Suspend an invocation and send the decision one level up the chain."""
        self.state.counters.escalations += 1
        self.state.invocations.pop(invocation.id, None)
        self.state.suspended[invocation.id] = invocation
        task = self.state.tasks.get(invocation.task_id) if invocation.task_id else None
        if task is not None:
            task.status = TaskStatus.ESCALATED
            task.escalation_count += 1
            level = task.escalation_count
        else:
            invocation.escalation_count += 1
            level = invocation.escalation_count
        self.route_escalation(self.climb(invocation.role, level - 1), invocation.id, reason)

    def climb(self, role: str, levels: int) -> str:
        """Walk up the chain. Repeat escalations go higher, so 'resume' can't loop forever.

        Each repeat reaches a more senior role, and the chain ends at a human, where
        nothing more is spent until someone decides.
        """
        current = role
        for _ in range(levels):
            above = self.org.role(current).escalation
            if above == "human":
                break
            current = above
        return current

    def route_escalation(self, from_role: str, origin_id: str, reason: str) -> None:
        target = self.org.role(from_role).escalation
        if target == "human":
            origin = self.state.suspended[origin_id]
            self.ask_human(
                f"Escalated by {from_role}: {reason}",
                ["resume", "abandon"],
                "escalation",
                task_id=origin.task_id,
                invocation_id=origin_id,
            )
            return
        self.activate(target.split("/", 1)[0], f"escalation from {from_role}")
        origin = self.state.suspended[origin_id]
        self.invoke(
            target,
            Purpose.ESCALATION,
            "EscalationResolution",
            task_id=origin.task_id,
            origin_invocation_id=origin_id,
            feedback=[reason],
        )

    def resume_origin(self, origin_id: str, guidance: str) -> None:
        origin = self.state.suspended.pop(origin_id)
        if guidance:
            origin.feedback.append(guidance)
        origin.attempts = 0
        self.state.invocations[origin.id] = origin
        if origin.task_id and origin.task_id in self.state.tasks:
            task = self.state.tasks[origin.task_id]
            task.status = TaskStatus.IN_REVIEW if origin.purpose == Purpose.REVIEW else TaskStatus.RUNNING
        self.emit(origin)

    def abandon_origin(self, origin_id: str, reason: str) -> None:
        origin = self.state.suspended.pop(origin_id)
        task = self.state.tasks.get(origin.task_id) if origin.task_id else None
        if task is None:
            self.fail_run(f"Abandoned {origin.role} ({origin.purpose.value}): {reason}")
            return
        task.status = TaskStatus.FAILED
        if task.task_id == FINAL_TASK_ID or task.work_package_id is None:
            self.fail_run(f"Final deliverable abandoned: {reason}")
            return
        self.state.packages[task.work_package_id].status = PackageStatus.FAILED
        self.fail_run(f"Task {task.task_id} abandoned: {reason}")
