"""Reviews and gates: which reviewer comes next, approval, waivers, and rework."""

from __future__ import annotations

from ..artifacts import (
    ReviewVerdict,
)
from .base import QA_GATE
from .escalation import StepEscalation
from .state import (
    FINAL_TASK_ID,
    Finish,
    Invocation,
    Purpose,
    RunPhase,
    TaskState,
    TaskStatus,
)


class StepReview(StepEscalation):
    def next_gate(self, task: TaskState) -> None:
        """Open the next required gate, or approve the task if none remain."""
        result_id = task.result_artifact_id
        assert result_id is not None
        while task.pending_gates:
            gate = task.pending_gates[0]
            if gate == QA_GATE:
                reviewer = task.reviewer_role
            else:
                configured = self.org.gates[gate]
                reviewer = configured.reviewer
                if not self.org.has_role(reviewer):
                    if configured.on_unavailable == "waive":
                        self.waive(task, gate, "org policy on_unavailable=waive", "org")
                        continue
                    self.ask_human(
                        f"Gate '{gate}' must review {task.task_id}, but {reviewer} is not installed. "
                        "Install the department, or waive this gate.",
                        ["waive", "abandon"],
                        "gate_unavailable",
                        task_id=task.task_id,
                        gate=gate,
                    )
                    return
                self.activate(reviewer.split("/", 1)[0], f"gate '{gate}' fired for {task.task_id}")
            if reviewer == task.owner_role:
                reviewer = self.org.role(task.owner_role).reviewer
            self.activate(reviewer.split("/", 1)[0], f"reviews {task.task_id}")
            task.status = TaskStatus.IN_REVIEW
            self.invoke(
                reviewer,
                Purpose.REVIEW,
                "ReviewVerdict",
                task_id=task.task_id,
                gate=gate,
                subject_artifact_id=result_id,
                context_refs=[
                    result_id,
                    *([task.brief_artifact_id] if task.brief_artifact_id else []),
                    *self.work_behind(task),
                    *self.earlier_verdicts(task, gate),
                ],
            )
            return
        self.approve(task)

    def earlier_verdicts(self, task: TaskState, gate: str) -> list[str]:
        """This gate's earlier verdicts on the same task. Without them, each review of reworked work started over and
        found new things, round after round, until real runs stopped at the rework limit."""
        return [
            artifact.artifact_id
            for artifact in self.state.artifacts.values()
            if artifact.type == "ReviewVerdict" and artifact.task_id == task.task_id
            and artifact.payload.get("gate") == gate
        ]  # fmt: skip

    def work_behind(self, task: TaskState) -> list[str]:
        """A final report summarizes other work. A reviewer given only the summary can't check it, and said so."""
        if task.task_id != FINAL_TASK_ID:
            return []
        return [t.result_artifact_id for t in self.state.tasks.values() if t.result_artifact_id and t is not task]

    def waive(self, task: TaskState, gate: str, note: str, by: str) -> None:
        task.pending_gates.remove(gate)
        task.passed_gates.append(gate)
        self.state.waivers.append({"task_id": task.task_id, "gate": gate, "note": note, "decided_by": by})

    def approve(self, task: TaskState) -> None:
        task.status = TaskStatus.APPROVED
        if task.task_id != FINAL_TASK_ID:
            return
        self.finish_or_reflect()

    def finish_or_reflect(self) -> None:
        """A small command that went smoothly has nothing to teach. One that was sent back or escalated does: it's
        where a company finds out it lacks a role, and real small commands were where that kept happening."""
        counters = self.state.counters
        troubled = counters.rework_loops or counters.escalations or counters.handoffs
        if self.state.size == "small" and not troubled:
            self.finish_done()
        else:
            self.start_retrospective()

    def finish_done(self) -> None:
        self.state.human_requests.clear()
        self.state.phase = RunPhase.DONE
        self.actions.append(Finish(status="done", final_artifact_id=self.state.final_artifact_id))

    def start_retrospective(self) -> None:
        self.invoke(self.org.root_role, Purpose.RETROSPECTIVE, "Retrospective")

    def request_rework(self, task: TaskState, reason: str) -> None:
        self.state.counters.rework_loops += 1
        task.rework_count += 1
        task.feedback.append(reason)
        task.passed_gates = []
        if task.rework_count > self.org.pipeline.max_rework:
            placeholder = Invocation(
                id=self.next_id("inv"),
                role=task.owner_role,
                purpose=Purpose.INTEGRATE if task.task_id == FINAL_TASK_ID else Purpose.EXECUTE,
                output_type=task.output_type,
                task_id=task.task_id,
                feedback=list(task.feedback),
            )
            self.state.invocations[placeholder.id] = placeholder
            self.escalate_invocation(placeholder, f"Rework limit reached: {reason}")
            return
        if task.task_id == FINAL_TASK_ID:
            task.status = TaskStatus.RUNNING
            self.invoke(
                task.owner_role,
                Purpose.INTEGRATE,
                "FinalDeliverable",
                task_id=FINAL_TASK_ID,
                feedback=list(task.feedback),
                context_refs=[r for r in [task.result_artifact_id] if r],
            )
            return
        task.status = TaskStatus.READY


def _verdict_problem(parsed: ReviewVerdict) -> str | None:
    if parsed.decision == "approve":
        serious = [i.description for i in parsed.issues if i.severity != "minor"]
        if serious:
            return f"An approval can't list major or blocker issues: {'; '.join(serious)}. Request changes instead."
    elif not parsed.issues:
        return f"A decision of '{parsed.decision}' must list at least one issue, saying what's wrong."
    return None
