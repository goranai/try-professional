"""Hierarchy invariants, enforced by the state machine and not by prompts."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from try_professional.core.pipeline import (
    ContextResolved,
    EscalationRaised,
    HandoffRaised,
    HumanDecided,
    IllegalEvent,
    InvocationFailed,
    Purpose,
    RunPhase,
    RunStarted,
    Submission,
    TaskStatus,
    UsageRecorded,
    advance,
    initial_state,
)
from try_professional.org import Org


def start(org: Org) -> tuple[Any, list[Any]]:
    return advance(initial_state("r1", "Goal"), RunStarted(), org)


CHARTER = {"original_goal": "Goal", "restated_goal": "Goal", "user_language": "en", "success_criteria": ["Works"]}


def to_plan(org: Org) -> Any:
    state, actions = start(org)
    state, actions = advance(
        state, Submission(invocation_id=actions[-1].invocation_id, actor=org.root_role, payload=CHARTER), org
    )
    return state, actions


PLAN = {
    "activated_departments": [{"id": "engineering", "reason": "code"}],
    "work_packages": [{"id": "wp1", "title": "Build", "owner_department": "engineering", "acceptance": ["ok"]}],
}


def to_dispatch(org: Org) -> Any:
    state, actions = to_plan(org)
    invoke = next(a for a in actions if a.kind == "invoke_role")
    return advance(state, Submission(invocation_id=invoke.invocation_id, actor=org.root_role, payload=PLAN), org)


class TestPurity:
    def test_advance_does_not_change_its_input(self, org: Org) -> None:
        state, actions = start(org)
        before = state.model_dump_json()
        advance(state, Submission(invocation_id=actions[-1].invocation_id, actor=org.root_role, payload=CHARTER), org)
        assert state.model_dump_json() == before

    def test_same_events_give_the_same_state(self, org: Org) -> None:
        assert to_dispatch(org)[0].model_dump_json() == to_dispatch(org)[0].model_dump_json()

    def test_starts_only_once(self, org: Org) -> None:
        state, _ = start(org)
        with pytest.raises(IllegalEvent, match="ALREADY_STARTED"):
            advance(state, RunStarted(), org)


class TestSingleOwner:
    def test_only_the_assigned_role_can_submit(self, org: Org) -> None:
        state, actions = start(org)
        with pytest.raises(IllegalEvent, match="WRONG_ACTOR"):
            advance(
                state,
                Submission(invocation_id=actions[-1].invocation_id, actor="marketing/social", payload=CHARTER),
                org,
            )

    def test_cannot_submit_to_a_closed_invocation(self, org: Org) -> None:
        state, _actions = to_plan(org)
        with pytest.raises(IllegalEvent, match="NO_OPEN_INVOCATION"):
            advance(state, Submission(invocation_id="inv-9999", actor=org.root_role, payload=CHARTER), org)

    def test_every_task_has_one_owner_and_a_definition_of_done(self, org: Org, harness: Callable[..., Any]) -> None:
        run = harness(org)
        result = run.runner.run("Goal")
        for task in run.orchestrator.state(result.run_id).tasks.values():
            assert task.owner_role and task.reviewer_role
            if task.work_package_id:
                assert task.definition_of_done

    def test_task_brief_reviewer_comes_from_the_org_not_the_head(self, org: Org, harness: Callable[..., Any]) -> None:
        run = harness(org)
        result = run.runner.run("Goal")
        state = run.orchestrator.state(result.run_id)
        brief = next(a for a in state.artifacts.values() if a.type == "TaskBrief")
        assert brief.payload["reviewer_role"] == org.resolve_reviewer(brief.payload["owner_role"])


class TestHierarchy:
    def _task_plan(self, owner: str, output: str = "CodeChange") -> dict[str, Any]:
        return {
            "work_package_id": "wp1",
            "tasks": [
                {
                    "id": "t1",
                    "title": "T",
                    "owner_role": owner,
                    "goal": "g",
                    "expected_output": "o",
                    "definition_of_done": ["d"],
                    "output_type": output,
                }
            ],
        }

    def test_head_cannot_assign_outside_its_department(self, org: Org) -> None:
        state, actions = to_dispatch(org)
        invoke = next(a for a in actions if a.kind == "invoke_role")
        state, actions = advance(
            state,
            Submission(
                invocation_id=invoke.invocation_id,
                actor=invoke.role,
                payload=self._task_plan("marketing/social", "TaskReport"),
            ),
            org,
        )
        assert state.tasks == {}
        retry = next(a for a in actions if a.kind == "invoke_role")
        assert retry.invocation_id == invoke.invocation_id
        assert "handoff" in state.invocations[invoke.invocation_id].feedback[0]

    def test_role_cannot_be_assigned_an_output_it_does_not_produce(self, org: Org) -> None:
        state, actions = to_dispatch(org)
        invoke = next(a for a in actions if a.kind == "invoke_role")
        state, _ = advance(
            state,
            Submission(
                invocation_id=invoke.invocation_id,
                actor=invoke.role,
                payload=self._task_plan("engineering/backend", "Plan"),
            ),
            org,
        )
        assert "produces" in state.invocations[invoke.invocation_id].feedback[0]

    def test_non_head_cannot_hand_off(self, org: Org) -> None:
        state, actions = to_dispatch(org)
        invoke = next(a for a in actions if a.kind == "invoke_role")
        state, actions = advance(
            state,
            Submission(
                invocation_id=invoke.invocation_id, actor=invoke.role, payload=self._task_plan("engineering/backend")
            ),
            org,
        )
        state, _ = advance(state, ContextResolved(task_id="wp1/t1", query="T"), org)
        running = next(i for i in state.invocations.values() if i.purpose == Purpose.EXECUTE)
        with pytest.raises(IllegalEvent, match="HANDOFF_NOT_HEAD"):
            advance(
                state,
                HandoffRaised(
                    invocation_id=running.id,
                    actor=running.role,
                    payload={
                        "from_department": "engineering",
                        "to_department": "marketing",
                        "request": "x",
                        "acceptance": ["y"],
                        "because": "z",
                    },
                ),
                org,
            )

    def test_head_cannot_hand_off_for_another_department(self, org: Org) -> None:
        state, actions = to_dispatch(org)
        invoke = next(a for a in actions if a.kind == "invoke_role")
        with pytest.raises(IllegalEvent, match="HANDOFF_WRONG_DEPARTMENT"):
            advance(
                state,
                HandoffRaised(
                    invocation_id=invoke.invocation_id,
                    actor=invoke.role,
                    payload={
                        "from_department": "sales",
                        "to_department": "marketing",
                        "request": "x",
                        "acceptance": ["y"],
                        "because": "z",
                    },
                ),
                org,
            )

    def test_handoff_runs_through_heads_and_resumes_the_requester(self, org: Org, harness: Callable[..., Any]) -> None:
        run = harness(org)
        run_id, _ = run.orchestrator.start("Goal")
        while True:
            state = run.orchestrator.state(run_id)
            dispatch = [i for i in state.invocations.values() if i.purpose == Purpose.DISPATCH]
            if dispatch:
                break
            for event in run.runner.deliver(run.orchestrator.open_actions(run_id)[0], run_id).events:
                run.orchestrator.handle(run_id, event)
        run.orchestrator.handle(
            run_id,
            HandoffRaised(
                invocation_id=dispatch[0].id,
                actor=dispatch[0].role,
                payload={
                    "from_department": "engineering",
                    "to_department": "research",
                    "request": "Find the API limits",
                    "acceptance": ["Limits are documented"],
                    "because": "Research has the analyst who reads vendor docs",
                },
            ),
        )
        state = run.orchestrator.state(run_id)
        assert "handoff from engineering" in state.activated["research"]
        assert state.counters.handoffs == 1
        assert dispatch[0].id in state.suspended

        result = run.runner.resume(run_id)
        assert result.status == "done"
        final = run.orchestrator.state(run_id)
        calls = [c["role"] for c in run.client.calls]
        assert calls.index("research/research-lead") < len(calls) - 1 - calls[::-1].index("engineering/tech-lead")
        handoff_pkg = next(p for p in final.packages.values() if p.origin == "handoff")
        assert handoff_pkg.status.value == "approved"
        assert any(a.type == "Handoff" for a in final.artifacts.values())

    def test_root_cannot_skip_planning_with_a_handoff(self, org: Org) -> None:
        """A real model tried this: the CEO handed work to Engineering instead of planning."""
        state, actions = to_plan(org)
        invoke = next(a for a in actions if a.kind == "invoke_role")
        with pytest.raises(IllegalEvent, match="HANDOFF_NOT_ALLOWED_HERE"):
            advance(
                state,
                HandoffRaised(
                    invocation_id=invoke.invocation_id,
                    actor=invoke.role,
                    payload={
                        "from_department": "executive",
                        "to_department": "engineering",
                        "request": "x",
                        "acceptance": ["y"],
                        "because": "z",
                    },
                ),
                org,
            )

    def test_only_root_can_amend_the_plan(self, org: Org) -> None:
        state, actions = to_dispatch(org)
        invoke = next(a for a in actions if a.kind == "invoke_role")
        state, actions = advance(
            state,
            Submission(
                invocation_id=invoke.invocation_id, actor=invoke.role, payload=self._task_plan("engineering/backend")
            ),
            org,
        )
        state, _ = advance(state, ContextResolved(task_id="wp1/t1", query="T"), org)
        running = next(i for i in state.invocations.values() if i.purpose == Purpose.EXECUTE)
        state, actions = advance(
            state, EscalationRaised(invocation_id=running.id, actor=running.role, payload={"reason": "stuck"}), org
        )
        head = next(a for a in actions if a.kind == "invoke_role")
        assert head.role == "engineering/tech-lead"
        state, _ = advance(
            state,
            Submission(
                invocation_id=head.invocation_id,
                actor=head.role,
                payload={
                    "decision": "amend_plan",
                    "added_work_packages": [
                        {"id": "x", "title": "x", "owner_department": "engineering", "acceptance": ["a"]}
                    ],
                },
            ),
            org,
        )
        assert "Only the root role" in state.invocations[head.invocation_id].feedback[-1]


class TestPlans:
    def _reject(self, org: Org, plan: dict[str, Any], expected: str) -> None:
        state, actions = to_plan(org)
        invoke = next(a for a in actions if a.kind == "invoke_role")
        state, _ = advance(
            state, Submission(invocation_id=invoke.invocation_id, actor=org.root_role, payload=plan), org
        )
        assert expected in state.invocations[invoke.invocation_id].feedback[-1]
        assert state.packages == {}

    def _package(self, id_: str, deps: list[str], department: str = "engineering") -> dict[str, Any]:
        return {"id": id_, "title": id_, "owner_department": department, "acceptance": ["a"], "depends_on": deps}

    def test_cycle_is_rejected(self, org: Org) -> None:
        self._reject(
            org,
            {
                "activated_departments": [{"id": "engineering", "reason": "r"}],
                "work_packages": [self._package("a", ["b"]), self._package("b", ["a"])],
            },
            "cycle",
        )

    def test_unknown_dependency_is_rejected(self, org: Org) -> None:
        self._reject(
            org,
            {
                "activated_departments": [{"id": "engineering", "reason": "r"}],
                "work_packages": [self._package("a", ["ghost"])],
            },
            "unknown package",
        )

    def test_owner_must_be_activated(self, org: Org) -> None:
        self._reject(
            org,
            {
                "activated_departments": [{"id": "engineering", "reason": "r"}],
                "work_packages": [self._package("a", [], "sales")],
            },
            "did not activate",
        )

    def test_empty_plan_fails_schema(self, org: Org) -> None:
        self._reject(org, {"activated_departments": [], "work_packages": []}, "invalid")

    def test_duplicate_package_ids_are_rejected(self, org: Org) -> None:
        self._reject(
            org,
            {
                "activated_departments": [{"id": "engineering", "reason": "r"}],
                "work_packages": [self._package("a", []), self._package("a", [])],
            },
            "unique",
        )


class TestReview:
    def _verdict(self, decision: str, sequence: list[str] | None = None) -> Any:
        remaining = list(sequence or [])

        def override(invocation: Any, state: Any, org: Org) -> dict[str, Any] | None:
            if invocation.purpose == Purpose.REVIEW and invocation.gate == "qa" and invocation.task_id != "__final__":
                choice = remaining.pop(0) if remaining else decision
                issues = [] if choice == "approve" else [{"severity": "major", "description": "Tests are missing"}]
                return {
                    "gate": invocation.gate,
                    "subject_artifact_id": invocation.subject_artifact_id,
                    "decision": choice,
                    "issues": issues,
                }
            return None

        return override

    def test_nothing_reaches_approved_without_a_verdict(self, org: Org) -> None:
        state, actions = to_dispatch(org)
        invoke = next(a for a in actions if a.kind == "invoke_role")
        state, actions = advance(
            state,
            Submission(
                invocation_id=invoke.invocation_id,
                actor=invoke.role,
                payload={
                    "work_package_id": "wp1",
                    "tasks": [
                        {
                            "id": "t1",
                            "title": "T",
                            "owner_role": "engineering/backend",
                            "goal": "g",
                            "expected_output": "o",
                            "definition_of_done": ["d"],
                            "output_type": "CodeChange",
                        }
                    ],
                },
            ),
            org,
        )
        state, actions = advance(state, ContextResolved(task_id="wp1/t1", query="T"), org)
        execute = next(a for a in actions if a.kind == "invoke_role")
        state, actions = advance(
            state,
            Submission(
                invocation_id=execute.invocation_id,
                actor=execute.role,
                payload={"summary": "done", "done_criteria_met": [{"criterion": "d", "met": True}]},
            ),
            org,
        )
        task = state.tasks["wp1/t1"]
        assert task.status == TaskStatus.IN_REVIEW
        review = next(a for a in actions if a.kind == "invoke_role")
        assert review.role == "engineering/qa" and review.gate == "qa"

        wrong = {"gate": "qa", "subject_artifact_id": "x", "decision": "approve",
                 "issues": [{"severity": "blocker", "description": "It doesn't build"}]}  # fmt: skip
        state, _ = advance(state, Submission(invocation_id=review.invocation_id, actor=review.role, payload=wrong), org)
        assert state.tasks["wp1/t1"].status == TaskStatus.IN_REVIEW
        assert "can't list major or blocker issues" in state.invocations[review.invocation_id].feedback[0]

        with pytest.raises(IllegalEvent, match="WRONG_ACTOR"):
            advance(
                state,
                Submission(
                    invocation_id=review.invocation_id,
                    actor=execute.role,
                    payload={"gate": "qa", "subject_artifact_id": task.result_artifact_id, "decision": "approve"},
                ),
                org,
            )

    def test_changes_requested_loops_back_with_the_reason(self, org: Org, harness: Callable[..., Any]) -> None:
        run = harness(org, self._verdict("approve", ["changes_requested", "approve"]))
        result = run.runner.run("Goal")
        assert result.status == "done"
        state = run.orchestrator.state(result.run_id)
        assert state.counters.rework_loops == 1
        task = next(t for t in state.tasks.values() if t.work_package_id)
        assert task.rework_count == 1
        assert "Tests are missing" in task.feedback[0]

    def test_rework_limit_escalates(self, org: Org, harness: Callable[..., Any]) -> None:
        run = harness(org, self._verdict("changes_requested"))
        result = run.runner.run("Goal")
        state = run.orchestrator.state(result.run_id)
        assert state.counters.rework_loops >= 3
        assert state.counters.escalations >= 1
        assert result.status == "blocked", "a reviewer that never approves must end at a human"

    @pytest.mark.parametrize("decision", ["changes_requested", "block"])
    def test_a_stubborn_reviewer_and_a_head_who_always_resumes_cannot_loop_forever(
        self, org: Org, harness: Callable[..., Any], decision: str
    ) -> None:
        """Without a bound, this pairing spends tokens forever. It must stop at a human."""
        run = harness(org, self._verdict(decision))
        result = run.runner.run("Goal")
        assert result.status == "blocked"
        assert len(run.client.calls) < 30
        roles = [c["role"] for c in run.client.calls if c["purpose"] == "escalation"]
        assert roles == ["engineering/tech-lead", "executive/ceo"], "each repeat goes one level higher"

    def test_repeat_failures_climb_the_chain_to_a_human(self, org: Org, harness: Callable[..., Any]) -> None:
        def override(invocation: Any, state: Any, org_: Org) -> dict[str, Any] | None:
            if invocation.purpose == Purpose.EXECUTE:
                return {"payload": {"never": "valid"}}
            return None

        run = harness(org, override)
        result = run.runner.run("Goal")
        assert result.status == "blocked"
        assert len(run.client.calls) < 200

    def test_block_verdict_escalates_to_a_human_when_it_reaches_the_top(
        self, org: Org, harness: Callable[..., Any]
    ) -> None:
        def override(invocation: Any, state: Any, org_: Org) -> dict[str, Any] | None:
            if invocation.purpose == Purpose.ESCALATION:
                return {"decision": "escalate_further", "guidance": "Above my level"}
            return self._verdict("block")(invocation, state, org_)

        run = harness(org, override)
        result = run.runner.run("Goal")
        assert result.status == "blocked"
        assert result.questions[0].options == ["resume", "abandon"]

        run.orchestrator.handle(
            result.run_id, HumanDecided(request_id=result.questions[0].request_id, decision="abandon", note="No")
        )
        state = run.orchestrator.state(result.run_id)
        assert state.phase == RunPhase.FAILED
        assert "No" in (state.failure_reason or "")

    def test_final_deliverable_must_cover_every_success_criterion(self, org: Org, harness: Callable[..., Any]) -> None:
        attempts = []

        def override(invocation: Any, state: Any, org_: Org) -> dict[str, Any] | None:
            if invocation.purpose == Purpose.INTEGRATE:
                attempts.append(1)
                if len(attempts) == 1:
                    return {
                        "summary_in_user_language": "s",
                        "criteria_results": [{"criterion": "Something else", "met": True}],
                    }
            return None

        run = harness(org, override)
        result = run.runner.run("Goal")
        assert result.status == "done"
        assert len(attempts) == 2


class TestFailures:
    def test_invalid_output_retries_then_escalates(self, org: Org) -> None:
        state, actions = start(org)
        invoke = actions[-1]
        for _ in range(org.pipeline.max_invalid_outputs):
            state, actions = advance(
                state, Submission(invocation_id=invoke.invocation_id, actor=org.root_role, payload={"bad": 1}), org
            )
            assert next(a for a in actions if a.kind == "invoke_role").invocation_id == invoke.invocation_id
        state, actions = advance(
            state, Submission(invocation_id=invoke.invocation_id, actor=org.root_role, payload={"bad": 1}), org
        )
        assert [a.kind for a in actions] == ["ask_human"]
        assert state.phase == RunPhase.BLOCKED
        assert state.counters.invalid_outputs == 3

    def test_non_retryable_failure_escalates_at_once(self, org: Org) -> None:
        state, actions = start(org)
        state, actions = advance(
            state, InvocationFailed(invocation_id=actions[-1].invocation_id, reason="refused", retryable=False), org
        )
        assert [a.kind for a in actions] == ["ask_human"]
        assert "refused" in actions[0].question

    def test_finished_run_accepts_nothing(self, org: Org, harness: Callable[..., Any]) -> None:
        run = harness(org)
        result = run.runner.run("Goal")
        with pytest.raises(IllegalEvent, match="RUN_FINISHED"):
            run.orchestrator.handle(
                result.run_id, EscalationRaised(invocation_id="x", actor="y", payload={"reason": "z"})
            )

    def test_human_answer_must_be_an_offered_option(self, org: Org) -> None:
        state, actions = start(org)
        state, actions = advance(
            state, InvocationFailed(invocation_id=actions[-1].invocation_id, reason="r", retryable=False), org
        )
        with pytest.raises(IllegalEvent, match="BAD_DECISION"):
            advance(state, HumanDecided(request_id=actions[0].request_id, decision="whatever"), org)


class TestBudget:
    def _limited(self, make_org: Callable[..., Org], limit: int) -> Org:
        def mutate(org_dir: Path) -> None:
            path = org_dir / "org.yaml"
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            doc["pipeline"]["budget"]["max_tokens_per_run"] = limit
            path.write_text(yaml.safe_dump(doc), encoding="utf-8")

        return make_org(mutate=mutate)

    def test_budget_blocks_and_stops_model_calls(
        self, make_org: Callable[..., Org], harness: Callable[..., Any]
    ) -> None:
        org = self._limited(make_org, 1)
        run = harness(org)
        result = run.runner.run("Goal")
        assert result.status == "blocked"
        assert "over its limit" in result.questions[0].question
        assert len(run.client.calls) == 1

    def test_human_can_raise_the_budget_and_continue(
        self, make_org: Callable[..., Org], harness: Callable[..., Any]
    ) -> None:
        org = self._limited(make_org, 1)
        run = harness(org)
        result = run.runner.run("Goal")
        run.orchestrator.handle(
            result.run_id,
            HumanDecided(request_id=result.questions[0].request_id, decision="resume", new_token_budget=10_000_000),
        )
        assert run.runner.resume(result.run_id).status == "done"

    def test_a_late_submission_cannot_clear_a_block(self, org: Org) -> None:
        state, actions = start(org)
        state, _ = advance(state, UsageRecorded(input_tokens=1, output_tokens=1), org)
        assert state.phase != RunPhase.BLOCKED
        limited = org.pipeline
        object.__setattr__(limited, "max_tokens_per_run", 1)
        try:
            state, _ = advance(state, UsageRecorded(input_tokens=5, output_tokens=5), org)
            assert state.phase == RunPhase.BLOCKED
            state, _ = advance(
                state, Submission(invocation_id=actions[-1].invocation_id, actor=org.root_role, payload=CHARTER), org
            )
            assert state.phase == RunPhase.BLOCKED
            state, again = advance(state, UsageRecorded(input_tokens=5, output_tokens=5), org)
            assert [a for a in again if a.kind == "ask_human"] == [], "one question per block"
            assert len(state.human_requests) == 1
        finally:
            object.__setattr__(limited, "max_tokens_per_run", None)

    def test_usage_after_finish_is_still_counted(self, org: Org) -> None:
        state, _ = start(org)
        state, _ = advance(state, UsageRecorded(input_tokens=5, output_tokens=7), org)
        assert state.usage.total == 12


class TestParallelism:
    def _wide_plan(self, width: int) -> Any:
        def override(invocation: Any, state: Any, org: Org) -> dict[str, Any] | None:
            if invocation.purpose == Purpose.DISPATCH:
                return {
                    "work_package_id": invocation.work_package_id,
                    "tasks": [
                        {
                            "id": f"t{n}",
                            "title": f"Task {n}",
                            "owner_role": "engineering/backend",
                            "goal": "g",
                            "expected_output": "o",
                            "definition_of_done": ["d"],
                            "output_type": "CodeChange",
                        }
                        for n in range(width)
                    ],
                }
            return None

        return override

    def test_running_tasks_never_exceed_the_limit(self, org: Org, harness: Callable[..., Any]) -> None:
        lock = threading.Lock()
        active = {"now": 0, "peak": 0}
        base = self._wide_plan(10)

        def override(invocation: Any, state: Any, org_: Org) -> dict[str, Any] | None:
            with lock:
                active["now"] += 1
                active["peak"] = max(active["peak"], active["now"])
            threading.Event().wait(0.02)
            with lock:
                active["now"] -= 1
            return base(invocation, state, org_)

        run = harness(org, override)
        result = run.runner.run("Goal")
        assert result.status == "done"
        assert 1 < active["peak"] <= org.pipeline.max_parallel_tasks

    def test_dependent_tasks_wait_for_approval(self, org: Org, harness: Callable[..., Any]) -> None:
        order: list[str] = []

        def override(invocation: Any, state: Any, org_: Org) -> dict[str, Any] | None:
            if invocation.purpose == Purpose.DISPATCH:
                task = {
                    "owner_role": "engineering/backend",
                    "goal": "g",
                    "expected_output": "o",
                    "definition_of_done": ["d"],
                    "output_type": "CodeChange",
                }
                return {
                    "work_package_id": invocation.work_package_id,
                    "tasks": [
                        {"id": "second", "title": "Second", "depends_on": ["first"], **task},
                        {"id": "first", "title": "First", **task},
                    ],
                }
            if invocation.purpose == Purpose.EXECUTE:
                if invocation.task_id.endswith("second"):
                    assert state.tasks["wp1/first"].status == TaskStatus.APPROVED
                    result_id = state.tasks["wp1/first"].result_artifact_id
                    assert result_id in invocation.context_refs
                order.append(invocation.task_id)
            return None

        run = harness(org, override)
        assert run.runner.run("Goal").status == "done"
        assert order == ["wp1/first", "wp1/second"]

    def test_task_dependency_cycle_is_rejected(self, org: Org) -> None:
        state, actions = to_dispatch(org)
        invoke = next(a for a in actions if a.kind == "invoke_role")
        task = {
            "title": "T",
            "owner_role": "engineering/backend",
            "goal": "g",
            "expected_output": "o",
            "definition_of_done": ["d"],
            "output_type": "CodeChange",
        }
        state, _ = advance(
            state,
            Submission(
                invocation_id=invoke.invocation_id,
                actor=invoke.role,
                payload={
                    "work_package_id": "wp1",
                    "tasks": [{"id": "a", "depends_on": ["b"], **task}, {"id": "b", "depends_on": ["a"], **task}],
                },
            ),
            org,
        )
        assert "cycle" in state.invocations[invoke.invocation_id].feedback[0]
