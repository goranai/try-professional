"""The pipeline: a pure function from (state, event) to (new state, actions).

No model calls, no clock, no I/O. Everything the hierarchy promises is enforced
here, so it is testable with no API key and can't be argued away by a prompt.
"""

from __future__ import annotations

from typing import Any

from ...org import Org
from ..artifacts import (
    Artifact,
    ArtifactError,
    Escalation,
    EscalationResolution,
    FinalDeliverable,
    GoalCharter,
    Handoff,
    Plan,
    ReviewVerdict,
    TaskBrief,
    TaskPlan,
    WorkPackage,
    make_artifact,
    parse_payload,
)
from ..gates import deterministic_flags, triggered_gates
from .state import (
    FINAL_TASK_ID,
    Action,
    AskHuman,
    ContextResolved,
    DepartmentActivated,
    EscalationRaised,
    Finish,
    HandoffRaised,
    HumanDecided,
    HumanRequest,
    Invocation,
    InvocationFailed,
    InvokeRole,
    PackageState,
    PackageStatus,
    Purpose,
    ResolveContext,
    RunPhase,
    RunStarted,
    RunState,
    Submission,
    TaskState,
    TaskStatus,
    Usage,
    UsageRecorded,
)

TERMINAL_PHASES = {RunPhase.DONE, RunPhase.FAILED}
QA_GATE = "qa"


class IllegalEvent(ValueError):
    """An event the rules forbid. It changes nothing and is recorded as a violation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class _Step:
    """Mutable working state for one advance() call."""

    def __init__(self, state: RunState, org: Org) -> None:
        self.state = state
        self.org = org
        self.actions: list[Action] = []

    # ---- ids and basics -----------------------------------------------------

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

    # ---- rejecting bad output ------------------------------------------------

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

    # ---- scheduling ------------------------------------------------------------

    def schedule(self) -> None:
        state = self.state
        if state.phase in TERMINAL_PHASES or state.phase == RunPhase.BLOCKED:
            return

        progressed = True
        while progressed:
            progressed = False
            for package in state.packages.values():
                if package.status == PackageStatus.ACTIVE and self.package_done(package.id):
                    package.status = PackageStatus.APPROVED
                    progressed = True
                    origin_id = package.resumes_invocation_id
                    siblings = [p for p in state.packages.values() if p.resumes_invocation_id == origin_id]
                    if (
                        origin_id in state.suspended
                        and origin_id is not None
                        and all(p.status == PackageStatus.APPROVED for p in siblings)
                    ):
                        for sibling in siblings:
                            sibling.resumes_invocation_id = None
                        self.resume_origin(origin_id, f"Work package '{package.id}' is approved.")

        for package in state.packages.values():
            if package.status == PackageStatus.PENDING and self.package_deps_met(package):
                package.status = PackageStatus.PLANNING
                head = self.org.departments[package.owner_department].head_ref
                self.invoke(head, Purpose.DISPATCH, "TaskPlan", work_package_id=package.id)

        for task in state.tasks.values():
            if task.status == TaskStatus.PENDING and self.task_deps_met(task):
                task.status = TaskStatus.RESOLVING_SKILLS
                self.actions.append(ResolveContext(task_id=task.task_id, role=task.owner_role, query=task.title))

        running = sum(1 for t in state.tasks.values() if t.status == TaskStatus.RUNNING)
        for task in state.tasks.values():
            if running >= self.org.pipeline.max_parallel_tasks:
                break
            if task.status == TaskStatus.READY:
                self.start_task(task)
                running += 1

        self.maybe_integrate()
        self.update_phase()

    def package_done(self, package_id: str) -> bool:
        tasks = [t for t in self.state.tasks.values() if t.work_package_id == package_id]
        return bool(tasks) and all(t.status == TaskStatus.APPROVED for t in tasks)

    def package_deps_met(self, package: PackageState) -> bool:
        return all(self.state.packages[dep].status == PackageStatus.APPROVED for dep in package.depends_on)

    def task_deps_met(self, task: TaskState) -> bool:
        return all(self.state.tasks[dep].status == TaskStatus.APPROVED for dep in task.depends_on)

    def start_task(self, task: TaskState) -> None:
        task.status = TaskStatus.RUNNING
        refs = [task.brief_artifact_id] if task.brief_artifact_id else []
        if task.skill_artifact_id:
            refs.append(task.skill_artifact_id)
        for dep in task.depends_on:
            result = self.state.tasks[dep].result_artifact_id
            if result:
                refs.append(result)
        self.invoke(
            task.owner_role,
            Purpose.EXECUTE,
            task.output_type,
            task_id=task.task_id,
            feedback=list(task.feedback),
            context_refs=refs,
        )

    def maybe_integrate(self) -> None:
        state = self.state
        if state.final_artifact_id or FINAL_TASK_ID in state.tasks or not state.packages:
            return
        if state.invocations or state.suspended:
            return
        if all(p.status == PackageStatus.APPROVED for p in state.packages.values()):
            root = self.org.root_role
            refs = [state.charter_artifact_id or ""]
            refs += [t.result_artifact_id for t in state.tasks.values() if t.result_artifact_id]
            final = TaskState(
                task_id=FINAL_TASK_ID,
                work_package_id=None,
                title="Assemble the final deliverable",
                owner_role=root,
                reviewer_role=root,
                output_type="FinalDeliverable",
                status=TaskStatus.RUNNING,
            )
            state.tasks[FINAL_TASK_ID] = final
            self.invoke(root, Purpose.INTEGRATE, "FinalDeliverable", task_id=FINAL_TASK_ID, context_refs=refs)

    def update_phase(self) -> None:
        state = self.state
        if state.phase in TERMINAL_PHASES or state.phase == RunPhase.BLOCKED:
            return
        purposes = {i.purpose for i in state.invocations.values()}
        statuses = {t.status for t in state.tasks.values()}
        if Purpose.RETROSPECTIVE in purposes:
            state.phase = RunPhase.RETROSPECTIVE
        elif Purpose.INTEGRATE in purposes:
            state.phase = RunPhase.INTEGRATION
        elif Purpose.DISPATCH in purposes:
            state.phase = RunPhase.DISPATCH
        elif TaskStatus.RESOLVING_SKILLS in statuses:
            state.phase = RunPhase.SKILL_RESOLUTION
        elif Purpose.EXECUTE in purposes or Purpose.ESCALATION in purposes:
            state.phase = RunPhase.EXECUTION
        elif Purpose.REVIEW in purposes:
            state.phase = RunPhase.REVIEW
        elif Purpose.PLAN in purposes:
            state.phase = RunPhase.PLANNING
        elif Purpose.INTAKE in purposes:
            state.phase = RunPhase.INTAKE

    # ---- gates ------------------------------------------------------------------

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
                context_refs=[result_id, *([task.brief_artifact_id] if task.brief_artifact_id else [])],
            )
            return
        self.approve(task)

    def waive(self, task: TaskState, gate: str, note: str, by: str) -> None:
        task.pending_gates.remove(gate)
        task.passed_gates.append(gate)
        self.state.waivers.append({"task_id": task.task_id, "gate": gate, "note": note, "decided_by": by})

    def approve(self, task: TaskState) -> None:
        task.status = TaskStatus.APPROVED
        if task.task_id == FINAL_TASK_ID:
            self.start_retrospective()

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


def _validate_plan(plan: Plan, org: Org) -> str | None:
    ids = [p.id for p in plan.work_packages]
    if len(ids) != len(set(ids)):
        return "work package IDs must be unique"
    activated = {a.id for a in plan.activated_departments}
    for activation in plan.activated_departments:
        if activation.id not in org.departments:
            return f"department '{activation.id}' is not installed. Installed departments: {', '.join(org.departments)}"
    for package in plan.work_packages:
        if package.owner_department not in org.departments:
            return f"package '{package.id}' is owned by uninstalled department '{package.owner_department}'"
        if package.owner_department not in activated and package.owner_department not in org.always_active():
            return f"package '{package.id}' is owned by '{package.owner_department}', which the plan did not activate"
        for dep in package.depends_on:
            if dep not in ids:
                return f"package '{package.id}' depends on unknown package '{dep}'"
    return _find_cycle({p.id: p.depends_on for p in plan.work_packages})


def _find_cycle(graph: dict[str, list[str]]) -> str | None:
    done: set[str] = set()

    def visit(node: str, trail: list[str]) -> str | None:
        if node in trail:
            return f"dependency cycle: {' -> '.join([*trail, node])}"
        if node in done:
            return None
        for dep in graph.get(node, []):
            found = visit(dep, [*trail, node])
            if found:
                return found
        done.add(node)
        return None

    for start in graph:
        found = visit(start, [])
        if found:
            return found
    return None


def _validate_task_plan(plan: TaskPlan, package: PackageState, org: Org) -> str | None:
    if plan.work_package_id != package.id:
        return f"work_package_id must be '{package.id}'"
    ids = [t.id for t in plan.tasks]
    if len(ids) != len(set(ids)):
        return "task IDs must be unique"
    department = org.departments[package.owner_department]
    for task in plan.tasks:
        if not org.has_role(task.owner_role) or task.owner_role.split("/", 1)[0] != department.id:
            members = ", ".join(f"{department.id}/{r}" for r in department.roles)
            return (
                f"task '{task.id}' is assigned to '{task.owner_role}'. A head assigns only within its "
                f"own department ({members}). For another department, use a handoff."
            )
        allowed = org.role(task.owner_role).outputs
        if task.output_type not in allowed:
            return f"task '{task.id}': {task.owner_role} produces {', '.join(allowed)}, not '{task.output_type}'"
        for dep in task.depends_on:
            if dep not in ids:
                return f"task '{task.id}' depends on unknown task '{dep}'"
    return _find_cycle({t.id: t.depends_on for t in plan.tasks})


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


def _on_submission(step: _Step, event: Submission) -> None:
    state, org = step.state, step.org
    invocation = _check_actor(state, event.invocation_id, event.actor)
    try:
        parsed = parse_payload(invocation.output_type, event.payload)
    except ArtifactError as error:
        step.reject(invocation, str(error))
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
        stored = artifact()
        state.charter_artifact_id = stored.artifact_id
        state.user_language = parsed.user_language
        close()
        step.invoke(org.root_role, Purpose.PLAN, "Plan", context_refs=[stored.artifact_id])

    elif invocation.purpose == Purpose.PLAN:
        assert isinstance(parsed, Plan)
        problem = _validate_plan(parsed, org)
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
        for spec in parsed.tasks:
            task_id = f"{owning.id}/{spec.id}"
            brief = TaskBrief(
                task_id=task_id,
                work_package_id=owning.id,
                title=spec.title,
                owner_role=spec.owner_role,
                reviewer_role=org.resolve_reviewer(spec.owner_role),
                definition_of_done=spec.definition_of_done,
                depends_on=[f"{owning.id}/{d}" for d in spec.depends_on],
                output_type=spec.output_type,
            )
            brief_artifact = step.store(
                make_artifact(
                    "TaskBrief",
                    brief.model_dump(mode="json"),
                    run_id=state.run_id,
                    produced_by=invocation.role,
                    task_id=task_id,
                    parents=[stored.artifact_id],
                    flags=owning.flags.union(spec.flags),
                )
            )
            state.tasks[task_id] = TaskState(
                task_id=task_id,
                work_package_id=owning.id,
                title=spec.title,
                owner_role=spec.owner_role,
                reviewer_role=brief.reviewer_role,
                definition_of_done=spec.definition_of_done,
                depends_on=brief.depends_on,
                output_type=spec.output_type,
                flags=owning.flags.union(spec.flags),
                brief_artifact_id=brief_artifact.artifact_id,
            )

    elif invocation.purpose == Purpose.EXECUTE:
        assert task is not None
        stored = artifact()
        close()
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
        stored = artifact()
        close()
        state.retrospective_artifact_id = stored.artifact_id
        state.human_requests.clear()
        state.phase = RunPhase.DONE
        step.actions.append(Finish(status="done", final_artifact_id=state.final_artifact_id))


def _add_package(step: _Step, package: WorkPackage, origin: Any, resumes: str | None = None) -> None:
    state = step.state
    if package.id in state.packages:
        raise IllegalEvent("DUPLICATE_PACKAGE", f"package '{package.id}' already exists")
    state.packages[package.id] = PackageState(
        id=package.id,
        title=package.title,
        owner_department=package.owner_department,
        depends_on=package.depends_on,
        acceptance=package.acceptance,
        flags=package.flags,
        origin=origin,
        resumes_invocation_id=resumes,
    )


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
        known = set(org.departments)
        activating = {a.id for a in parsed.activate_departments}
        for activation in parsed.activate_departments:
            if activation.id not in known:
                step.reject(invocation, f"department '{activation.id}' is not installed")
                return
        for package in parsed.added_work_packages:
            if package.owner_department not in known:
                step.reject(invocation, f"department '{package.owner_department}' is not installed")
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


def _on_usage(step: _Step, event: UsageRecorded) -> None:
    state = step.state
    buckets = [state.usage]
    if event.role:
        buckets.append(state.usage_by_role.setdefault(event.role, Usage()))
    for bucket in buckets:
        bucket.input_tokens += event.input_tokens
        bucket.output_tokens += event.output_tokens
        bucket.cached_input_tokens += event.cached_input_tokens
        bucket.calls += 1
    limit = state.budget_override or step.org.pipeline.max_tokens_per_run
    if limit is None or state.usage.total <= limit or state.phase in TERMINAL_PHASES:
        return
    if state.phase == RunPhase.BLOCKED:
        return
    step.ask_human(
        f"This run used {state.usage.total} tokens, over the limit of {limit}. "
        "Continue with a higher limit, or abandon?",
        ["resume", "abandon"],
        "budget",
    )


def _on_human(step: _Step, event: HumanDecided) -> None:
    state = step.state
    request = state.human_requests.pop(event.request_id, None)
    if request is None:
        raise IllegalEvent("NO_OPEN_REQUEST", f"human request '{event.request_id}' is not open")
    if event.decision not in request.options:
        state.human_requests[request.id] = request
        raise IllegalEvent("BAD_DECISION", f"'{event.decision}' is not one of {request.options}")

    if not state.human_requests:
        state.phase = RunPhase.EXECUTION

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
    elif request.kind == "budget" and event.new_token_budget:
        state.budget_override = event.new_token_budget


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
    else:
        raise IllegalEvent("UNKNOWN_EVENT", type(event).__name__)

    step.schedule()
    _check_stall(step)
    return step.state, step.actions


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
