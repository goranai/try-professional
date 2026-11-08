"""Installed is not invoked: the §5.3 table from docs/architecture.md, one test per row."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from try_professional.core.pipeline import HumanDecided, Purpose, RunPhase
from try_professional.org import Org

COMMERCIAL = {"marketing", "sales"}


def _plan(*departments: str, flags: dict[str, bool] | None = None) -> Any:
    def override(invocation: Any, state: Any, org: Org) -> dict[str, Any] | None:
        if invocation.purpose != Purpose.PLAN:
            return None
        return {
            "activated_departments": [{"id": d, "reason": "needed"} for d in departments],
            "work_packages": [
                {
                    "id": f"wp-{d}",
                    "title": f"Work for {d}",
                    "owner_department": d,
                    "acceptance": ["done"],
                    "flags": flags or {},
                }
                for d in departments
            ],
        }

    return override


def _departments(roles: set[str] | list[str]) -> set[str]:
    return {r.split("/")[0] for r in roles}


def test_code_only_goal_never_touches_marketing_or_sales(org: Org, harness: Callable[..., Any]) -> None:
    run = harness(org, _plan("engineering"))
    result = run.runner.run("Fix the login bug")
    assert result.status == "done"

    assert not COMMERCIAL & _departments(run.called_roles()), "model calls"
    assert not COMMERCIAL & _departments(run.memory.recalls), "memory opens"
    assert not COMMERCIAL & _departments(run.skills.searches), "skill queries"

    state = run.orchestrator.state(result.run_id)
    assert set(state.activated) == {"executive", "engineering"}
    assert run.memory.recalls and run.skills.searches, "the checks above must not pass vacuously"


def test_audit_log_shows_marketing_and_sales_were_never_involved(org: Org, harness: Callable[..., Any]) -> None:
    run = harness(org, _plan("engineering"))
    result = run.runner.run("Fix the login bug")
    roles = set()
    for record in run.records(result.run_id):
        data = record["data"]
        for key in ("role", "actor", "department"):
            if key in data:
                roles.add(data[key].split("/")[0])
    assert "engineering" in roles
    assert not COMMERCIAL & roles


def test_personal_data_wakes_legal_though_the_plan_left_it_out(org: Org, harness: Callable[..., Any]) -> None:
    def override(invocation: Any, state: Any, org_: Org) -> dict[str, Any] | None:
        if invocation.purpose == Purpose.EXECUTE:
            return {
                "summary": "Stores each customer's email and phone number",
                "done_criteria_met": [{"criterion": "Done as described", "met": True}],
            }
        return _plan("engineering")(invocation, state, org_)

    run = harness(org, override)
    result = run.runner.run("Add user accounts")
    assert result.status == "done"
    state = run.orchestrator.state(result.run_id)
    assert "legal" in state.activated
    assert "gate 'legal' fired" in state.activated["legal"]
    assert "legal/counsel" in run.called_roles()


def test_goal_mentioning_personal_data_reaches_legal_at_integration(org: Org, harness: Callable[..., Any]) -> None:
    run = harness(org, _plan("engineering"))
    result = run.runner.run("Export all customer data to a spreadsheet")
    assert result.status == "done"
    assert "legal/counsel" in run.called_roles()


def test_plan_cannot_skip_legal_for_public_output(org: Org, harness: Callable[..., Any]) -> None:
    run = harness(org, _plan("marketing"))
    result = run.runner.run("Write a launch post")
    assert result.status == "done"

    state = run.orchestrator.state(result.run_id)
    assert "legal" not in {a["id"] for a in state.artifacts[state.plan_artifact_id].payload["activated_departments"]}
    verdicts = [a for a in state.artifacts.values() if a.type == "ReviewVerdict"]
    assert {"qa", "legal"} <= {v.payload["gate"] for v in verdicts}
    assert all(t.passed_gates for t in state.tasks.values() if t.output_type == "PublicContent")


def test_model_declaring_no_flags_cannot_avoid_the_gate(org: Org, harness: Callable[..., Any]) -> None:
    def override(invocation: Any, state: Any, org_: Org) -> dict[str, Any] | None:
        if invocation.purpose == Purpose.EXECUTE:
            return {
                "payload": {
                    "audience": "public",
                    "channel": "web",
                    "locale": "en",
                    "body": "Hello",
                    "done_criteria_met": [{"criterion": "Done as described", "met": True}],
                },
                "flags": {"public_facing": False},
            }
        return _plan("marketing")(invocation, state, org_)

    run = harness(org, override)
    result = run.runner.run("Write a launch post")
    state = run.orchestrator.state(result.run_id)
    assert "legal/counsel" in run.called_roles()
    assert all(a.flags.public_facing for a in state.artifacts.values() if a.type == "PublicContent")


def _gate_without_legal(org_dir: Any) -> None:
    """The legal gate configured, and its department gone: edited by hand, since removing the pack removes both."""
    import yaml

    from try_professional.packs import DEFAULT_GATES

    path = org_dir / "org.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["gates"] = yaml.safe_load(yaml.safe_dump(DEFAULT_GATES))
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_without_legal_there_is_no_legal_gate(make_org: Callable[..., Org], harness: Callable[..., Any]) -> None:
    org = make_org(["executive", "marketing"])
    assert org.gates == {}
    result = harness(org, _plan("marketing")).runner.run("Write a launch post")
    assert result.status == "done"


def test_missing_legal_pack_blocks_and_escalates(make_org: Callable[..., Org], harness: Callable[..., Any]) -> None:
    org = make_org(["executive", "marketing"], mutate=_gate_without_legal)
    run = harness(org, _plan("marketing"))
    result = run.runner.run("Write a launch post")
    assert result.status == "blocked"
    assert "legal/counsel" in result.questions[0].question
    assert set(result.questions[0].options) == {"waive", "abandon"}
    assert run.orchestrator.state(result.run_id).phase == RunPhase.BLOCKED


def test_human_waiver_continues_and_is_logged(make_org: Callable[..., Org], harness: Callable[..., Any]) -> None:
    org = make_org(["executive", "marketing"], mutate=_gate_without_legal)
    run = harness(org, _plan("marketing"))
    result = run.runner.run("Write a launch post")

    for _ in range(4):
        if result.status != "blocked":
            break
        question = result.questions[0]
        run.orchestrator.handle(
            result.run_id, HumanDecided(request_id=question.request_id, decision="waive", note="internal draft only")
        )
        result = run.runner.resume(result.run_id)

    assert result.status == "done"
    state = run.orchestrator.state(result.run_id)
    assert state.waivers and all(w["gate"] == "legal" and w["decided_by"] == "human" for w in state.waivers)
    assert any(
        r["kind"] == "event" and r["data"].get("note") == "internal draft only" for r in run.records(result.run_id)
    )


def test_plan_naming_an_uninstalled_department_is_rejected(
    make_org: Callable[..., Org], harness: Callable[..., Any]
) -> None:
    org = make_org(["executive", "engineering"])
    attempts = []

    def override(invocation: Any, state: Any, org_: Org) -> dict[str, Any] | None:
        if invocation.purpose == Purpose.PLAN:
            attempts.append(list(invocation.feedback))
            if len(attempts) == 1:
                return _plan("marketing")(invocation, state, org_)
            return _plan("engineering")(invocation, state, org_)
        return None

    run = harness(org, override)
    result = run.runner.run("Do something")
    assert result.status == "done"
    assert "not installed" in attempts[1][0]
    assert "marketing" not in run.orchestrator.state(result.run_id).activated


def test_qa_runs_for_every_activated_department_and_owner_never_reviews(org: Org, harness: Callable[..., Any]) -> None:
    departments = ["engineering", "research", "finance", "sales"]
    run = harness(org, _plan(*departments))
    result = run.runner.run("Multi-department goal")
    assert result.status == "done"
    state = run.orchestrator.state(result.run_id)
    real_tasks = [t for t in state.tasks.values() if t.work_package_id]
    assert {t.owner_role.split("/")[0] for t in real_tasks} == set(departments)
    verdicts = [a for a in state.artifacts.values() if a.type == "ReviewVerdict"]
    for task in real_tasks:
        assert "qa" in task.passed_gates
        reviews = [v for v in verdicts if v.payload["subject_artifact_id"] == task.result_artifact_id]
        assert reviews
        assert all(v.produced_by != task.owner_role for v in reviews)


def test_head_can_escalate_for_a_logged_plan_amendment(org: Org, harness: Callable[..., Any]) -> None:
    """Under-activation is recoverable: head to CEO to plan amendment."""
    escalated = {"done": False}

    def override(invocation: Any, state: Any, org_: Org) -> dict[str, Any] | None:
        if invocation.purpose == Purpose.ESCALATION and invocation.role == "executive/ceo":
            return {
                "decision": "amend_plan",
                "guidance": "Research first",
                "activate_departments": [{"id": "research", "reason": "Engineering needs market data"}],
                "added_work_packages": [
                    {"id": "wp-extra", "title": "Market data", "owner_department": "research", "acceptance": ["done"]}
                ],
            }
        return _plan("engineering")(invocation, state, org_)

    run = harness(org, override)
    run_id, _ = run.orchestrator.start("Build a thing")
    state = None
    while True:
        state = run.orchestrator.state(run_id)
        open_invocations = list(state.invocations.values())
        dispatch = [i for i in open_invocations if i.purpose == Purpose.DISPATCH]
        if dispatch and not escalated["done"]:
            from try_professional.core.pipeline import EscalationRaised

            escalated["done"] = True
            run.orchestrator.handle(
                run_id,
                EscalationRaised(
                    invocation_id=dispatch[0].id, actor=dispatch[0].role, payload={"reason": "Need market data"}
                ),
            )
            continue
        if open_invocations and not dispatch and not escalated["done"]:
            event = run.runner.deliver(run.orchestrator.open_actions(run_id)[0], run_id).events
            for item in event:
                run.orchestrator.handle(run_id, item)
            continue
        break

    result = run.runner.resume(run_id)
    assert result.status == "done"
    state = run.orchestrator.state(result.run_id)
    assert state.activated["research"] == "Engineering needs market data"
    assert state.packages["wp-extra"].origin == "amendment"
    assert state.counters.escalations == 1
