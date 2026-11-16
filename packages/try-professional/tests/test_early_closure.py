"""A deliberate early finish must not stop the scheduler with a failed run."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from try_professional.adapters.mcp import McpService
from try_professional.cli import _print_result, cmd_status
from try_professional.core.pipeline import EscalationRaised, HandoffRaised, Purpose, Submission, advance
from try_professional.core.pipeline.state import Invocation, RunState
from try_professional.org import Org

REASON = "The approved findings show that the remaining work is no longer needed."


def waiting_for_decision(
    org: Org,
    harness: Callable[..., Any],
    stage: Purpose = Purpose.EXECUTE,
    *,
    small: bool = False,
    handoff: bool = False,
) -> tuple[Any, str, Invocation, Invocation]:
    def plan(invocation: Invocation, state: RunState, org_: Org) -> dict[str, Any] | None:
        if small and invocation.purpose == Purpose.INTAKE:
            return {
                "original_goal": state.goal,
                "restated_goal": state.goal,
                "user_language": "en",
                "success_criteria": ["The goal is met"],
                "size": "small",
                "route_department": "engineering",
            }
        if invocation.purpose == Purpose.PLAN:
            return {
                "activated_departments": [{"id": "engineering", "reason": "Owns the work"}],
                "work_packages": [
                    {
                        "id": f"wp{n}",
                        "title": f"Work {n}",
                        "owner_department": "engineering",
                        "acceptance": ["Reviewed"],
                        "depends_on": [f"wp{n - 1}"] if n > 1 else [],
                    }
                    for n in range(1, 4)
                ],
            }
        return None

    run = harness(org, plan)
    run.orchestrator.deliverables_dir = run.orchestrator.home / "deliverables"
    run_id, _ = run.orchestrator.start("Finish when further work is unnecessary")
    for _ in range(40):
        state = run.orchestrator.state(run_id)
        origin = next(
            (
                i
                for i in state.invocations.values()
                if i.purpose == stage and (small or i.work_package_id == "wp2" or (i.task_id or "").startswith("wp2/"))
            ),
            None,
        )
        if origin is not None:
            break
        action = run.orchestrator.open_actions(run_id)[0]
        for event in run.runner.deliver(action, run_id).events:
            run.orchestrator.handle(run_id, event)
    else:
        raise AssertionError("Work never reached the requested stage")

    if handoff:
        run.orchestrator.handle(
            run_id,
            HandoffRaised(
                invocation_id=origin.id,
                actor=origin.role,
                payload={
                    "from_department": "engineering",
                    "to_department": "research",
                    "request": "Assess whether further work is necessary",
                    "acceptance": ["Reviewed findings"],
                    "because": "Research can evaluate the findings",
                },
            ),
        )
        origin = next(i for i in run.orchestrator.state(run_id).invocations.values() if i.role.startswith("research/"))

    run.orchestrator.handle(
        run_id, EscalationRaised(invocation_id=origin.id, actor=origin.role, payload={"reason": REASON})
    )
    for _ in range(8):
        state = run.orchestrator.state(run_id)
        decision = next(i for i in state.invocations.values() if i.purpose == Purpose.ESCALATION)
        if decision.role == org.root_role:
            return run, run_id, origin, decision
        run.orchestrator.handle(
            run_id,
            Submission(invocation_id=decision.id, actor=decision.role, payload={"decision": "escalate_further"}),
        )
    raise AssertionError("Escalation never reached the CEO")


@pytest.mark.parametrize("stage", [Purpose.DISPATCH, Purpose.EXECUTE, Purpose.REVIEW])
def test_ceo_early_closure_finishes_delivery_and_retrospective_and_reports_why(
    org: Org,
    harness: Callable[..., Any],
    stage: Purpose,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import argparse

    from try_professional import cli

    run, run_id, origin, decision = waiting_for_decision(org, harness, stage)
    run.orchestrator.handle(
        run_id,
        Submission(
            invocation_id=decision.id, actor=org.root_role, payload={"decision": "close_early", "guidance": REASON}
        ),
    )
    closed = run.orchestrator.state(run_id)
    assert closed.early_closure_reason == REASON
    assert closed.early_closed_by == org.root_role
    assert closed.packages["wp1"].status.value == "approved"
    assert [closed.packages[p].status.value for p in ("wp2", "wp3")] == ["not_needed", "not_needed"]
    assert origin.id not in closed.suspended
    assert all(t.status.value == "not_needed" for t in closed.tasks.values() if t.work_package_id == "wp2")
    assert all(i.purpose == Purpose.INTEGRATE for i in closed.invocations.values())
    integration = next(iter(closed.invocations.values()))
    discarded = {t.result_artifact_id for t in closed.tasks.values() if t.status.value == "not_needed"}
    assert not discarded.intersection(integration.context_refs)

    result = run.runner.resume(run_id)
    final = run.orchestrator.state(run_id)
    assert result.status == "done"
    assert final.final_artifact_id and final.retrospective_artifact_id
    assert final.failure_reason is None and not final.cancelled
    assert _print_result(result) == 0
    assert REASON in capsys.readouterr().out
    assert REASON in "\n".join(result.final["known_limits"])
    for service in (McpService(run.orchestrator), McpService(run.orchestrator, bound_role=org.root_role)):
        status = service.run_status(run_id)
        assert status["phase"] == "done" and status["early_closure_reason"] == REASON
        assert status["early_closed_by"] == org.root_role
    index = run.orchestrator.deliverables_path(run_id) / "index.md"
    text = index.read_text(encoding="utf-8")
    assert "Closed early" in text and REASON in text and "not_needed" in text
    records = run.records(run_id)
    closure = [r["data"] for r in records if r["kind"] == "run_closed_early"]
    assert closure == [{"reason": REASON, "decided_by": org.root_role, "not_needed_packages": ["wp2", "wp3"]}]
    assert run.orchestrator.verify_audit(run_id).ok
    assert run.orchestrator.company.team_status([origin.role])[0]["open"] == 0

    monkeypatch.setattr(cli, "load_config", lambda _: type("Config", (), {"org_dir": "", "home": "", "raw": {}})())
    monkeypatch.setattr(cli, "load_org", lambda _: org)
    monkeypatch.setattr(cli, "Orchestrator", lambda *args, **kwargs: run.orchestrator)
    assert cmd_status(argparse.Namespace(role=None, run_id=run_id)) == 0
    assert REASON in capsys.readouterr().out


def test_early_closure_clears_outstanding_handoffs_from_company_status_and_head_briefs(
    harness: Callable[..., Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A completed early closure still told both heads to wait on an unnecessary handoff in company status."""
    import json
    import sqlite3

    from try_professional.cli import main
    from try_professional.org import load_org

    monkeypatch.chdir(tmp_path)
    assert main(["init", "--skip-model"]) == 0
    org = load_org(tmp_path / "org")
    run, run_id, _, decision = waiting_for_decision(org, harness, Purpose.DISPATCH, handoff=True)
    store = run.orchestrator.company
    assert len(store.open_handoffs()) == 1
    assert store.handoffs_for("engineering")["waiting_on"]
    assert store.handoffs_for("research")["others_wait_on_me"]

    run.orchestrator.handle(
        run_id,
        Submission(
            invocation_id=decision.id, actor=org.root_role, payload={"decision": "close_early", "guidance": REASON}
        ),
    )
    assert run.runner.resume(run_id).status == "done"
    state = run.orchestrator.state(run_id)
    assert state.final_artifact_id and state.retrospective_artifact_id
    assert store.open_handoffs() == []
    with sqlite3.connect(store.path) as saved:
        assert saved.execute("SELECT status FROM handoffs WHERE run_id = ?", (run_id,)).fetchall() == [("not_needed",)]

    (tmp_path / "trypro.yaml").write_text(json.dumps({"home": run.orchestrator.home.as_posix()}), encoding="utf-8")
    run.orchestrator.close()
    capsys.readouterr()
    assert main(["status"]) == 0
    assert "Open handoffs:\n  None." in capsys.readouterr().out
    for ref in ("engineering/tech-lead", "research/research-lead"):
        assert main(["status", "--role", ref]) == 0
        brief = capsys.readouterr().out
        assert "You're waiting on:" not in brief
        assert "Others are waiting on you:" not in brief


def test_abandoning_work_that_cannot_be_done_still_fails_the_run(
    org: Org, harness: Callable[..., Any], capsys: pytest.CaptureFixture[str]
) -> None:
    run, run_id, _, decision = waiting_for_decision(org, harness)
    reason = "The required work cannot be done."
    run.orchestrator.handle(
        run_id,
        Submission(invocation_id=decision.id, actor=org.root_role, payload={"decision": "abandon", "guidance": reason}),
    )
    result = run.runner.resume(run_id)
    state = run.orchestrator.state(run_id)
    assert result.status == "failed" and _print_result(result) == 1
    assert reason in capsys.readouterr().out
    assert state.packages["wp2"].status.value == "failed"
    assert state.tasks["wp2/t1"].status.value == "failed"
    assert not state.final_artifact_id and not state.retrospective_artifact_id
    assert not any(r["kind"] == "run_closed_early" for r in run.records(run_id))
    assert run.orchestrator.verify_audit(run_id).ok


@pytest.mark.parametrize("stage", [Purpose.DISPATCH, Purpose.EXECUTE])
def test_a_small_run_closed_early_still_has_a_retrospective(
    org: Org, harness: Callable[..., Any], stage: Purpose
) -> None:
    run, run_id, _, decision = waiting_for_decision(org, harness, stage, small=True)
    run.orchestrator.handle(
        run_id,
        Submission(
            invocation_id=decision.id, actor=org.root_role, payload={"decision": "close_early", "guidance": REASON}
        ),
    )
    assert run.runner.resume(run_id).status == "done"
    assert run.orchestrator.state(run_id).retrospective_artifact_id


@pytest.mark.parametrize("guidance", ["", "   "])
def test_early_closure_requires_a_reason(org: Org, harness: Callable[..., Any], guidance: str) -> None:
    run, run_id, _, decision = waiting_for_decision(org, harness)
    before = run.orchestrator.state(run_id)
    run.orchestrator.handle(
        run_id,
        Submission(
            invocation_id=decision.id, actor=org.root_role, payload={"decision": "close_early", "guidance": guidance}
        ),
    )
    state = run.orchestrator.state(run_id)
    assert state.packages == before.packages and state.suspended == before.suspended
    assert "reason" in state.invocations[decision.id].feedback[-1]


def test_only_the_ceo_can_close_a_run_early(org: Org, harness: Callable[..., Any]) -> None:
    run, run_id, _, decision = waiting_for_decision(org, harness)
    decision.role = "engineering/tech-lead"
    state = run.orchestrator.state(run_id)
    state.invocations[decision.id] = decision
    state, _ = advance(
        state,
        Submission(
            invocation_id=decision.id, actor=decision.role, payload={"decision": "close_early", "guidance": REASON}
        ),
        org,
    )
    assert "Only the root role" in state.invocations[decision.id].feedback[-1]
    assert state.packages["wp3"].status.value == "pending"


def test_early_closure_cannot_skip_final_delivery_or_hide_a_failed_package(
    org: Org, harness: Callable[..., Any]
) -> None:
    from try_professional.core.pipeline.state import FINAL_TASK_ID, PackageStatus, TaskState

    run, run_id, _, decision = waiting_for_decision(org, harness)
    state = run.orchestrator.state(run_id)
    state.tasks[FINAL_TASK_ID] = TaskState(
        task_id=FINAL_TASK_ID,
        work_package_id=None,
        title="Final delivery",
        owner_role=org.root_role,
        reviewer_role=org.root_role,
        output_type="FinalDeliverable",
    )
    event = Submission(
        invocation_id=decision.id, actor=org.root_role, payload={"decision": "close_early", "guidance": REASON}
    )
    refused, _ = advance(state, event, org)
    assert refused.early_closure_reason is None
    assert "before final delivery" in refused.invocations[decision.id].feedback[-1]
    state.tasks.pop(FINAL_TASK_ID)
    state.packages["wp2"].status = PackageStatus.FAILED
    refused, _ = advance(state, event, org)
    assert refused.early_closure_reason is None
    assert refused.packages["wp2"].status == PackageStatus.FAILED
