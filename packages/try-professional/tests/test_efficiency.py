"""Specialists without the group-chat failure: no work passed around, and every run says what it cost in time.

People who run many bots in one group chat see them loop, and see one hand another what it could have done itself.
Here a role can't message another at all, and the one channel between departments is guarded.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from try_professional import Company
from try_professional.adapters.mcp.claude_code import _COMMAND
from try_professional.core.efficiency import measure
from try_professional.core.pipeline import HandoffRaised, Purpose
from try_professional.org import Org


def _to_dispatch(run: Any, run_id: str, department: str) -> Any:
    """Run until a head of `department` is assigning work, and return that step."""
    for _ in range(60):
        state = run.orchestrator.state(run_id)
        found = [i for i in state.invocations.values()
                 if i.purpose == Purpose.DISPATCH and i.role.startswith(f"{department}/")]  # fmt: skip
        if found:
            return found[0]
        action = run.orchestrator.open_actions(run_id)[0]
        for event in run.runner.deliver(action, run_id).events:
            run.orchestrator.handle(run_id, event)
    raise AssertionError(f"{department} never dispatched")


def _hand_off(run: Any, run_id: str, step: Any, to: str, because: str = "they have the right role") -> list[str]:
    run.orchestrator.handle(
        run_id,
        HandoffRaised(
            invocation_id=step.id,
            actor=step.role,
            payload={
                "from_department": step.role.split("/")[0],
                "to_department": to,
                "request": "Do this part",
                "acceptance": ["It's done"],
                **({"because": because} if because else {}),
            },
        ),
    )
    state = run.orchestrator.state(run_id)
    still_open = state.invocations.get(step.id)
    return list(still_open.feedback) if still_open else []


class TestNoPassingWorkAround:
    def test_a_handoff_says_what_the_other_department_has(self, org: Org, harness: Callable[..., Any]) -> None:
        run = harness(org)
        run_id, _ = run.orchestrator.start("Goal")
        step = _to_dispatch(run, run_id, "engineering")
        feedback = _hand_off(run, run_id, step, "research", because="")
        assert any("because" in item for item in feedback)
        assert run.orchestrator.state(run_id).counters.handoffs == 0

    def test_work_cant_go_back_to_the_department_that_asked(self, org: Org, harness: Callable[..., Any]) -> None:
        run = harness(org)
        run_id, _ = run.orchestrator.start("Goal")
        engineering = _to_dispatch(run, run_id, "engineering")
        assert _hand_off(run, run_id, engineering, "research") == []
        research = _to_dispatch(run, run_id, "research")
        feedback = _hand_off(run, run_id, research, "engineering")
        assert any("engineering asked for this work" in item for item in feedback)

    def test_a_handoff_of_a_handoff_is_the_last(self, org: Org, harness: Callable[..., Any]) -> None:
        run = harness(org)
        run_id, _ = run.orchestrator.start("Goal")
        assert _hand_off(run, run_id, _to_dispatch(run, run_id, "engineering"), "research") == []
        assert _hand_off(run, run_id, _to_dispatch(run, run_id, "research"), "marketing") == []
        feedback = _hand_off(run, run_id, _to_dispatch(run, run_id, "marketing"), "sales")
        assert any("already handed on 2 times (engineering -> research -> marketing)" in item for item in feedback)

    def test_the_playbooks_ask_for_the_fewest_roles(self) -> None:
        from try_professional import playbooks

        ceo, manager = playbooks.section("ceo", "intake"), playbooks.section("manager", "dispatch")
        assert "When unsure, choose small" in ceo and "When unsure, choose large" not in ceo
        assert "If one role can do it all, give it one task" in manager
        assert "Never send work back to the department that asked you" in manager
        assert "token_budget" not in playbooks.section("ceo", "plan"), "no budgets asked for"


class TestRolesWorkAtTheSameTime:
    def test_claude_code_starts_every_ready_role_at_once(self) -> None:
        assert "start all of them at once, in one message" in _COMMAND
        assert "one at a time" not in _COMMAND

    def test_the_coordinator_is_told_when_several_roles_are_ready(self, org: Org, harness: Callable[..., Any]) -> None:
        """Haiku followed the rule in /company only partly, so the server says it at the moment it matters."""
        from try_professional.adapters.mcp import McpService
        from try_professional.core.pipeline import InvokeRole

        run = harness(org)
        run_id, _ = run.orchestrator.start("Goal")
        coordinator = McpService(run.orchestrator, coordinator=True)
        assert "start_together" not in coordinator.run_next_action(run_id), "one role ready: nothing to say"

        def ready(*roles: str) -> Any:
            return lambda _: [InvokeRole(invocation_id=f"i{n}", role=role, purpose=Purpose.EXECUTE, output_type="x")
                              for n, role in enumerate(roles)]  # fmt: skip

        run.orchestrator.open_actions = ready("marketing/content-writer", "sales/proposal-writer")  # type: ignore[method-assign]
        answer = coordinator.run_next_action(run_id)
        assert "2 roles are ready" in answer["start_together"]
        assert "Start all their subagents now, in one message" in answer["start_together"]
        run.orchestrator.open_actions = ready("sales/proposal-writer", "sales/proposal-writer")  # type: ignore[method-assign]
        assert "start_together" not in coordinator.run_next_action(run_id), "one role, two steps: one subagent"


class TestEveryRunSaysWhatItCost:
    def test_time_calls_and_extra_rounds(self, tmp_path: Any) -> None:
        company = Company.create(tmp_path / "acme", name="Acme")
        result = company.run("Write a launch note", dry_run=True)
        report = company.efficiency(result.run_id)
        assert report["status"] == "done"
        assert report["calls"] >= 2 and report["tokens"] > 0
        assert set(report["steps"]) >= {"intake", "execute", "review"}
        assert report["calls_by_purpose"]["review"] >= 1
        assert 0 <= report["review_share"] <= 1
        assert report["handoffs"] == [] and report["rework"] == 0

    def test_time_waiting_for_a_person_isnt_the_companys(self, tmp_path: Any) -> None:
        import json

        audit = tmp_path / "audit.jsonl"
        records = [
            {"kind": "run_started", "ts": "2026-11-09T10:00:00+00:00", "data": {}},
            {"kind": "phase", "ts": "2026-11-09T10:02:00+00:00", "data": {"from": "execution", "to": "blocked"}},
            {"kind": "phase", "ts": "2026-11-09T11:02:00+00:00", "data": {"from": "blocked", "to": "execution"}},
            {"kind": "phase", "ts": "2026-11-09T11:05:00+00:00", "data": {"from": "execution", "to": "done"}},
        ]
        audit.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        report = measure(audit, "r")
        assert report.waiting_seconds == 3600 and report.working_seconds == 300
        assert report.lines()[0].startswith("Took 5.0 min of work, plus 60.0 waiting for a person")


class TestReviewsConverge:
    """Reworked work drew a new list of issues each round, until real runs stopped at the rework limit."""

    def test_criteria_reworded_in_place_still_count(self) -> None:
        from try_professional.core.pipeline.base import _unreported

        wanted = [
            "The plan contains exactly five Instagram posts for the bakery's first week.",
            "Each post has a caption.",
        ]
        said = [{"criterion": "The plan has five posts for week one"}, {"criterion": "Every post has its caption"}]
        assert _unreported(wanted, said) == []
        assert _unreported(wanted, said[:1]) == wanted, "with one left out, nothing can be paired by place"
        assert _unreported(wanted, [said[0], {"criterion": "The logo is blue"}]) == ["Each post has a caption."]
