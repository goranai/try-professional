"""Planning and scheduling: plans, task plans, routing small commands, and starting work when it's ready."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ...org import Org, Role
from ..artifacts import (
    WORK_OUTPUT_TYPES,
    Artifact,
    DepartmentActivation,
    GoalCharter,
    OrgChange,
    Plan,
    TaskBrief,
    TaskPlan,
    TaskSpec,
    WorkPackage,
    make_artifact,
)
from ..gates import deterministic_flags
from .base import MAX_TASKS_PER_PACKAGE, MAX_WORK_PACKAGES, TERMINAL_PHASES, IllegalEvent, _not_done, _same_words
from .review import StepReview
from .state import (
    FINAL_TASK_ID,
    PackageState,
    PackageStatus,
    Purpose,
    ResolveContext,
    RunPhase,
    TaskState,
    TaskStatus,
)

if TYPE_CHECKING:
    from .step import _Step


class StepPlanning(StepReview):
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
                self.invoke(
                    head,
                    Purpose.DISPATCH,
                    "TaskPlan",
                    work_package_id=package.id,
                    context_refs=self.upstream_results(package.id),
                )

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

    def upstream_results(self, package_id: str | None) -> list[str]:
        """The approved work of every package this one depends on.

        A plan says one package builds on another, so that work has to arrive. It can't be left to a model to pass on:
        a real scriptwriter was once told to use an approved list that nobody could give it.
        """
        seen: set[str] = set()
        waiting = list(self.state.packages[package_id].depends_on) if package_id in self.state.packages else []
        while waiting:
            current = waiting.pop()
            if current not in seen:
                seen.add(current)
                waiting += self.state.packages[current].depends_on
        return [
            t.result_artifact_id
            for t in self.state.tasks.values()
            if t.work_package_id in seen and t.status == TaskStatus.APPROVED and t.result_artifact_id
        ]

    def start_task(self, task: TaskState) -> None:
        task.status = TaskStatus.RUNNING
        refs = [task.brief_artifact_id] if task.brief_artifact_id else []
        if task.skill_artifact_id:
            refs.append(task.skill_artifact_id)
        refs += self.upstream_results(task.work_package_id)
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
            if self.assemble_one_role_final():
                return
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

    def assemble_one_role_final(self) -> bool:
        """A small command one role did needs no final report from the CEO, and its summary no second legal review.

        Its brief is the command's success criteria, word for word, and its work already passed QA, and Legal if
        Legal's triggers applied. A model writing a summary of it, and a reviewer reading that summary, added two
        model calls and nothing a person needs. So code assembles the report from the approved work, and adds what
        a person must be told, as it does for every report. Any other command still gets the CEO's report.
        """
        state = self.state
        tasks = [t for t in state.tasks.values() if t.task_id != FINAL_TASK_ID]
        if state.size != "small" or len(tasks) != 1:
            return False
        task = tasks[0]
        charter = state.artifacts.get(state.charter_artifact_id or "")
        result = state.artifacts.get(task.result_artifact_id or "")
        if charter is None or result is None or task.status != TaskStatus.APPROVED:
            return False
        criteria = charter.payload["success_criteria"]
        if [_same_words(c) for c in task.definition_of_done] != [_same_words(c) for c in criteria]:
            return False
        reported = {_same_words(item["criterion"]): item for item in result.payload.get("done_criteria_met") or []}
        payload = {
            "summary_in_user_language": result.payload.get("summary") or result.payload.get("body") or task.title,
            "artifact_refs": [result.artifact_id],
            "criteria_results": [
                {
                    "criterion": criterion,
                    "met": bool(reported.get(_same_words(criterion), {}).get("met")),
                    "evidence": str(reported.get(_same_words(criterion), {}).get("evidence") or ""),
                }
                for criterion in criteria
            ],
            "known_limits": _not_done(state),
        }
        final = TaskState(
            task_id=FINAL_TASK_ID,
            work_package_id=None,
            title="Assemble the final deliverable",
            owner_role=self.org.root_role,
            reviewer_role=self.org.root_role,
            output_type="FinalDeliverable",
            status=TaskStatus.APPROVED,
            flags=task.flags,
        )
        stored = self.store(
            make_artifact(
                "FinalDeliverable",
                payload,
                run_id=state.run_id,
                produced_by="orchestrator",
                task_id=FINAL_TASK_ID,
                language=state.user_language,
                parents=[charter.artifact_id, result.artifact_id],
                flags=task.flags,
            )
        )
        final.result_artifact_id = stored.artifact_id
        state.tasks[FINAL_TASK_ID] = final
        state.final_artifact_id = stored.artifact_id
        self.finish_or_reflect()
        return True

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


def _validate_plan(plan: Plan, org: Org, limit: int | None = None) -> str | None:
    ids = [p.id for p in plan.work_packages]
    if len(ids) != len(set(ids)):
        return "work package IDs must be unique"
    if len(ids) > MAX_WORK_PACKAGES:
        return f"a plan can have at most {MAX_WORK_PACKAGES} work packages, not {len(ids)}"
    allocated = sum(p.token_budget for p in plan.work_packages)
    if limit is not None and allocated > limit:
        return f"package budgets add up to {allocated} tokens, over this run's limit of {limit}"
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
    if len(ids) > MAX_TASKS_PER_PACKAGE:
        return f"a work package can have at most {MAX_TASKS_PER_PACKAGE} tasks, not {len(ids)}"
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


def _work_output(outputs: tuple[str, ...]) -> str | None:
    return next((output for output in outputs if output in WORK_OUTPUT_TYPES), None)


def _settled_route(charter: GoalCharter) -> GoalCharter:
    """A role named on its own is clear once its department is known, so that isn't worth a retry."""
    role, department = charter.route_role, charter.route_department
    if role and department and "/" not in role:
        return charter.model_copy(update={"route_role": f"{department}/{role}"})
    return charter


def _route_problem(charter: GoalCharter, org: Org) -> str | None:
    """Why a small command can't go where the root role sent it. Gates still apply wherever it goes."""
    root_department = org.root_role.split("/", 1)[0]
    department = charter.route_department or charter.route_role.split("/", 1)[0]
    if department not in org.departments or department == root_department:
        known = ", ".join(d for d in org.departments if d != root_department)
        return f"a small command needs route_department, one of: {known}"
    role = charter.route_role
    if not role:
        return None
    if not org.has_role(role) or role.split("/", 1)[0] != department:
        known = ", ".join(f"{department}/{name}" for name in org.departments[department].roles)
        return f"route_role '{role}' isn't a role in '{department}'. Its roles: {known}"
    if _work_output(org.role(role).outputs) is None:
        return f"route_role '{role}' doesn't produce work results. Route to the department instead"
    return None


def org_change_problem(changes: list[OrgChange], org: Org) -> str | None:
    """A proposed role or department a person could approve as written. Nothing is changed here."""
    for change in changes:
        if change.kind == "add_role":
            if change.department not in org.departments:
                known = ", ".join(org.departments)
                return f"add_role needs an existing department, one of: {known}. For a new one, use add_department"
            if f"{change.department}/{change.role_id}" in org.roles:
                return f"{change.department}/{change.role_id} already exists. Propose only what the company lacks"
        elif change.department in org.departments:
            return f"Department '{change.department}' already exists. Use add_role to add to it"
    return None


def _role_names(role: Role) -> set[str]:
    return {name.lower() for name in (role.id.replace("-", " ").replace("_", " "), role.display_name) if len(name) > 3}


def named_elsewhere(charter: GoalCharter, org: Org) -> list[str]:
    """Roles outside the routed department that the command's own criteria name, such as "confirmed by the validator".

    A small command goes to one department, so those roles would never be asked. A real company's CEO sized six hunts
    small, and its Validator never ran once, while every review asked for its check. A name that's also a role of the
    routed department, of the root's, or a reviewer's, says nothing about who must do the work.
    """
    department = charter.route_department or charter.route_role.split("/", 1)[0]
    root_department = org.root_role.split("/", 1)[0]
    reviewers = {d.qa_reviewer for d in org.departments.values() if d.qa_reviewer}
    reviewers |= {gate.reviewer for gate in org.gates.values()}
    common: set[str] = set()
    for ref, role in org.roles.items():
        if role.department in (department, root_department) or ref in reviewers:
            common |= _role_names(role)
    text = " ".join([*charter.success_criteria, *charter.constraints]).lower()
    named = []
    for ref, role in org.roles.items():
        if role.department in (department, root_department) or ref in reviewers:
            continue
        names = _role_names(role) - common
        if any(re.search(rf"\b{re.escape(name)}s?\b", text) for name in names):
            named.append(f"{role.display_name} ({ref})")
    return named


def _start_small(step: _Step, charter: GoalCharter, stored: Artifact) -> None:
    """Skip the steps a small command doesn't need: planning, and dispatch if one role can do it alone."""
    state, org = step.state, step.org
    state.size = "small"
    department = charter.route_department or charter.route_role.split("/", 1)[0]
    package = WorkPackage(
        id="wp1",
        title=charter.restated_goal[:120],
        owner_department=department,
        acceptance=charter.success_criteria,
        flags=deterministic_flags("GoalCharter", charter.model_dump(mode="json")),
    )
    reason = "small command, routed by the root role"
    plan = Plan(activated_departments=[DepartmentActivation(id=department, reason=reason)], work_packages=[package])
    plan_artifact = step.store(
        make_artifact(
            "Plan",
            plan.model_dump(mode="json"),
            run_id=state.run_id,
            produced_by="orchestrator",
            parents=[stored.artifact_id],
        )
    )
    state.plan_artifact_id = plan_artifact.artifact_id
    step.activate(department, reason)
    _add_package(step, package, "plan")
    if not charter.route_role:
        return
    owning = state.packages[package.id]
    owning.status = PackageStatus.ACTIVE
    output_type = _work_output(org.role(charter.route_role).outputs)
    assert output_type is not None
    spec = TaskSpec(
        id="t1",
        title=charter.restated_goal[:120],
        owner_role=charter.route_role,
        goal=charter.restated_goal,
        context="; ".join(charter.constraints),
        expected_output=f"A {output_type} that meets the definition of done",
        definition_of_done=charter.success_criteria,
        output_type=output_type,
    )
    _add_tasks(step, owning, [spec], "orchestrator", plan_artifact.artifact_id)


def _add_tasks(step: _Step, owning: PackageState, specs: list[TaskSpec], produced_by: str, parent: str) -> None:
    state, org = step.state, step.org
    for spec in specs:
        task_id = f"{owning.id}/{spec.id}"
        brief = TaskBrief(
            task_id=task_id,
            work_package_id=owning.id,
            title=spec.title,
            owner_role=spec.owner_role,
            reviewer_role=org.resolve_reviewer(spec.owner_role),
            goal=spec.goal,
            context=spec.context,
            inputs=spec.inputs,
            expected_output=spec.expected_output,
            relevant_memory=spec.relevant_memory,
            relevant_skills=spec.relevant_skills,
            definition_of_done=spec.definition_of_done,
            depends_on=[f"{owning.id}/{d}" for d in spec.depends_on],
            output_type=spec.output_type,
        )
        brief_artifact = step.store(
            make_artifact(
                "TaskBrief",
                brief.model_dump(mode="json"),
                run_id=state.run_id,
                produced_by=produced_by,
                task_id=task_id,
                parents=[parent],
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
        token_budget=package.token_budget,
    )
