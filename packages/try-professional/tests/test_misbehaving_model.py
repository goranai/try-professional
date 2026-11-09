"""What happens when a model misbehaves. Anything that matters must hold whichever model is running.

Every test here plays a bad model on the scripted client. The system must reject, retry within limits, or stop
cleanly, and must never silently produce a wrong result. A prompt can ask for good behaviour, and only code can hold it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from try_professional.adapters.api_runner.clients import ChatRequest, ChatResponse, ToolCall
from try_professional.adapters.api_runner.runner import ApiRunner, ModelRoutes
from try_professional.adapters.api_runner.scripted import ScriptedClient, default_payload
from try_professional.adapters.api_runner.tools import ESCALATE, SUBMIT
from try_professional.adapters.mcp.service import McpService, ToolError
from try_professional.core.lock import StateLock
from try_professional.core.orchestrator import Orchestrator
from try_professional.core.pipeline import Purpose, Submission
from try_professional.core.pipeline.machine import _token_limit
from try_professional.core.pipeline.state import FINAL_TASK_ID
from try_professional.org import Org, load_org
from try_professional.packs import available_packs, install_org

from .conftest import LARGE_LIMIT as DEFAULT_MAX_TOKENS_PER_RUN
from .conftest import SMALL_LIMIT as DEFAULT_MAX_TOKENS_PER_SMALL_COMMAND
from .conftest import with_limits

ROUTES = ModelRoutes({"standard": "m", "executive": "m", "fast": "m"})


class BadModel(ScriptedClient):
    """Answers each call with whatever `reply` returns, falling back to good behaviour on None."""

    def __init__(self, reply: Callable[[Any, Any, Org, ChatRequest], Any]) -> None:
        super().__init__()
        self.reply = reply

    def complete(self, request: ChatRequest) -> ChatResponse:
        invocation, state, org = request.metadata["invocation"], request.metadata["state"], request.metadata["org"]
        answer = self.reply(invocation, state, org, request)
        if answer is None:
            return super().complete(request)
        self.calls.append({"role": invocation.role, "purpose": invocation.purpose.value, "tools": []})
        calls = answer if isinstance(answer, list) else []
        text = answer if isinstance(answer, str) else ""
        return ChatResponse(
            text=text,
            tool_calls=calls,
            stop_reason="tool_use" if calls else "end_turn",
            assistant_message={"role": "assistant", "content": text},
            input_tokens=10,
            output_tokens=10,
            model="bad",
        )


def _submit(payload: Any, **extra: Any) -> list[ToolCall]:
    return [ToolCall(id="c1", name=SUBMIT, arguments={"payload": payload, **extra})]


@pytest.fixture
def org(tmp_path: Path) -> Org:
    install_org(tmp_path / "org", "Test", available_packs())
    # These tests are about stopping a misbehaving model, so their company has limits to stop at.
    with_limits(tmp_path / "org")
    return load_org(tmp_path / "org")


@pytest.fixture
def company(org: Org, tmp_path: Path) -> Iterator[Orchestrator]:
    orchestrator = Orchestrator(org, tmp_path / "home")
    yield orchestrator
    orchestrator.close()


def _run(company: Orchestrator, reply: Callable[..., Any]) -> tuple[Any, BadModel]:
    client = BadModel(reply)
    return ApiRunner(company, client, ROUTES, max_turns=8).run("Write a launch note"), client


def _limit_tokens(company: Orchestrator, run_id: str, limit: int) -> None:
    state = company.state(run_id)
    state.budget_override = limit
    company.ledger.save(state, company._log(run_id).head, company.clock())


def _stopped_cleanly(result: Any) -> bool:
    return result.status in ("blocked", "failed")


def _no_final(company: Orchestrator, result: Any) -> bool:
    return company.state(result.run_id).final_artifact_id is None


class TestMalformedOutput:
    @pytest.mark.parametrize(
        "bad",
        ["not an object", 42, [], None, {}],
        ids=["text", "number", "list", "null", "empty"],
    )
    def test_a_payload_of_the_wrong_shape_is_never_accepted(self, company: Orchestrator, bad: Any) -> None:
        result, _ = _run(company, lambda *_: _submit(bad))
        assert _stopped_cleanly(result) and _no_final(company, result)
        assert company.state(result.run_id).charter_artifact_id is None

    def test_broken_json_goes_back_to_the_model_and_stops(self, company: Orchestrator) -> None:
        def reply(*_: Any) -> list[ToolCall]:
            return [ToolCall(id="c1", name=SUBMIT, arguments={}, parse_error="That wasn't valid JSON.")]

        result, client = _run(company, reply)
        assert _stopped_cleanly(result) and _no_final(company, result)
        assert len(client.calls) < 60, "retries have a limit"

    def test_extra_fields_have_no_effect(self, company: Orchestrator) -> None:
        """Extra fields are how a model would add a tool, a gate, or a budget nobody allowed. They're dropped, so they
        can't: the field leaves no trace, and every review still happens."""

        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose in (Purpose.INTAKE, Purpose.EXECUTE):
                return _submit({**default_payload(invocation, state, org), "skip_review": True})
            return None

        result, _ = _run(company, reply)
        state = company.state(result.run_id)
        assert all("skip_review" not in artifact.payload for artifact in state.artifacts.values())
        work = [t for t in state.tasks.values() if t.result_artifact_id and t.task_id != FINAL_TASK_ID]
        assert work and all("qa" in t.passed_gates for t in work if t.status.value == "approved")
        assert any(a.type == "ReviewVerdict" for a in state.artifacts.values())

    def test_missing_fields_are_rejected(self, company: Orchestrator) -> None:
        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.INTAKE:
                payload = default_payload(invocation, state, org)
                del payload["success_criteria"]
                return _submit(payload)
            return None

        result, _ = _run(company, reply)
        assert _stopped_cleanly(result)
        assert company.state(result.run_id).charter_artifact_id is None

    def test_spaces_cant_pass_as_content(self, company: Orchestrator) -> None:
        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.EXECUTE:
                return _submit(
                    {"summary": "   ", "done_criteria_met": [{"criterion": "Done as described", "met": True}]}
                )
            return None

        result, _ = _run(company, reply)
        assert _stopped_cleanly(result) and _no_final(company, result)


class TestSkippingSteps:
    def test_a_plan_cant_stand_in_for_the_charter(self, company: Orchestrator) -> None:
        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.INTAKE:
                return _submit(
                    {
                        "activated_departments": [{"id": "marketing", "reason": "r"}],
                        "work_packages": [
                            {"id": "wp", "title": "t", "owner_department": "marketing", "acceptance": ["a"]}
                        ],
                    }
                )
            return None

        result, client = _run(company, reply)
        assert _stopped_cleanly(result)
        assert "plan" not in [c["purpose"] for c in client.calls]

    def test_a_final_report_cant_come_before_the_work(self, company: Orchestrator) -> None:
        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.PLAN:
                return _submit(
                    {
                        "summary_in_user_language": "All done",
                        "criteria_results": [{"criterion": "The goal is met", "met": True}],
                    }
                )
            return None

        result, _ = _run(company, reply)
        assert _stopped_cleanly(result) and _no_final(company, result)

    def test_calling_a_command_small_doesnt_skip_review(self, company: Orchestrator) -> None:
        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.INTAKE:
                small = {"size": "small", "route_department": "marketing", "route_role": "marketing/content-writer"}
                return _submit({**default_payload(invocation, state, org), **small})
            return None

        result, client = _run(company, reply)
        assert result.status == "done"
        assert "review" in [c["purpose"] for c in client.calls]

    def test_a_final_report_must_cover_every_success_criterion(self, company: Orchestrator) -> None:
        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.INTEGRATE:
                return _submit(
                    {
                        "summary_in_user_language": "All done",
                        "criteria_results": [{"criterion": "Something else entirely", "met": True}],
                    }
                )
            return None

        result, _ = _run(company, reply)
        assert result.status != "done"


class TestInventedThings:
    def test_an_invented_tool_is_refused_and_recorded(self, company: Orchestrator) -> None:
        seen: dict[str, str] = {}

        def reply(invocation: Any, state: Any, org: Org, request: ChatRequest) -> Any:
            if invocation.purpose == Purpose.EXECUTE and "done" not in seen:
                seen["done"] = "yes"
                return [ToolCall(id="c1", name="delete_everything", arguments={"path": "/"})]
            return None

        result, _ = _run(company, reply)
        assert result.status == "done"
        assert company.state(result.run_id).counters.violations >= 1

    def test_a_tool_the_role_may_not_use_is_refused(self, company: Orchestrator) -> None:
        seen: dict[str, str] = {}

        def reply(invocation: Any, state: Any, org: Org, request: ChatRequest) -> Any:
            if invocation.purpose == Purpose.INTAKE and "done" not in seen:
                seen["done"] = "yes"
                return [ToolCall(id="c1", name="workspace_write", arguments={"path": "x.md", "content": "y"})]
            return None

        result, _ = _run(company, reply)
        assert not (company.workspace(result.run_id) / "x.md").exists()
        assert company.state(result.run_id).counters.violations >= 1

    def test_an_invented_department_is_rejected(self, company: Orchestrator) -> None:
        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.PLAN:
                return _submit(
                    {
                        "activated_departments": [{"id": "human", "reason": "ask a person"}],
                        "work_packages": [{"id": "w", "title": "t", "owner_department": "human", "acceptance": ["a"]}],
                    }
                )
            return None

        result, _ = _run(company, reply)
        assert _stopped_cleanly(result)
        assert "human" not in company.state(result.run_id).activated

    def test_work_cant_be_given_to_another_department(self, company: Orchestrator) -> None:
        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.DISPATCH:
                payload = default_payload(invocation, state, org)
                payload["tasks"][0]["owner_role"] = "legal/counsel"
                return _submit(payload)
            return None

        result, _ = _run(company, reply)
        assert _stopped_cleanly(result)
        assert not [t for t in company.state(result.run_id).tasks.values() if t.owner_role == "legal/counsel"]

    def test_a_file_it_never_wrote_cant_be_claimed(self, company: Orchestrator) -> None:
        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.EXECUTE and invocation.output_type == "TaskReport":
                wanted = state.tasks[invocation.task_id].definition_of_done
                return _submit(
                    {
                        "summary": "Wrote the report",
                        "outputs": ["report.md"],
                        "done_criteria_met": [{"criterion": c, "met": True} for c in wanted],
                    }
                )
            return None

        def plan(invocation: Any, state: Any, org: Org, request: ChatRequest) -> Any:
            if invocation.purpose == Purpose.DISPATCH:
                payload = default_payload(invocation, state, org)
                payload["tasks"][0]["output_type"] = "TaskReport"
                payload["tasks"][0]["owner_role"] = "operations/support"
                return _submit(payload)
            if invocation.purpose == Purpose.PLAN:
                return _submit(
                    {
                        "activated_departments": [{"id": "operations", "reason": "r"}],
                        "work_packages": [
                            {"id": "wp", "title": "t", "owner_department": "operations", "acceptance": ["a"]}
                        ],
                    }
                )
            return reply(invocation, state, org, request)

        result, _ = _run(company, plan)
        assert _stopped_cleanly(result) and _no_final(company, result)

    def test_an_unsourced_claim_must_say_so(self, company: Orchestrator) -> None:
        from try_professional.core.artifacts import ArtifactError, parse_payload

        base = {"audience": "a", "channel": "c", "locale": "en", "body": "b"}
        with pytest.raises(ArtifactError, match="unverified"):
            parse_payload("PublicContent", {**base, "claims": [{"claim": "Doubles your sales"}]})
        parse_payload("PublicContent", {**base, "claims": [{"claim": "Doubles your sales", "unverified": True}]})


class TestIgnoringTheBrief:
    def test_work_that_doesnt_report_on_the_brief_is_rejected(self, company: Orchestrator) -> None:
        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.EXECUTE:
                return _submit({"summary": "I wrote a poem instead"})
            return None

        result, client = _run(company, reply)
        assert _stopped_cleanly(result) and _no_final(company, result)
        assert "review" not in [c["purpose"] for c in client.calls], "it never reached review"

    def test_inventing_its_own_criteria_doesnt_count(self, company: Orchestrator) -> None:
        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.EXECUTE:
                criteria = [{"criterion": "I decided this is fine", "met": True}]
                return _submit({"summary": "s", "done_criteria_met": criteria})
            return None

        result, _ = _run(company, reply)
        assert _stopped_cleanly(result) and _no_final(company, result)

    def test_what_wasnt_met_reaches_the_person_even_if_the_final_report_omits_it(self, company: Orchestrator) -> None:
        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.EXECUTE:
                wanted = state.tasks[invocation.task_id].definition_of_done
                criteria = [{"criterion": c, "met": False, "evidence": "couldn't"} for c in wanted]
                payload = default_payload(invocation, state, org)
                return _submit({**payload, "done_criteria_met": criteria})
            if invocation.purpose == Purpose.INTEGRATE:
                payload = default_payload(invocation, state, org)
                return _submit({**payload, "known_limits": []})
            return None

        result, _ = _run(company, reply)
        assert result.status == "done"
        assert any("Not met" in line and "Done as described" in line for line in result.final["known_limits"])

    def test_unverified_findings_reach_the_person(self, company: Orchestrator) -> None:
        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.PLAN:
                return _submit(
                    {
                        "activated_departments": [{"id": "research", "reason": "r"}],
                        "work_packages": [
                            {"id": "wp", "title": "t", "owner_department": "research", "acceptance": ["a"]}
                        ],
                    }
                )
            if invocation.purpose == Purpose.EXECUTE:
                wanted = state.tasks[invocation.task_id].definition_of_done
                return _submit(
                    {
                        "question": "q",
                        "findings": [{"statement": "Tool X costs $5", "confidence": "low", "unverified": True}],
                        "done_criteria_met": [{"criterion": c, "met": True} for c in wanted],
                    }
                )
            if invocation.purpose == Purpose.INTEGRATE:
                return _submit({**default_payload(invocation, state, org), "known_limits": []})
            return None

        result, _ = _run(company, reply)
        assert result.status == "done"
        assert any("Tool X costs $5" in line for line in result.final["known_limits"])


class TestDishonestReviews:
    def test_approval_with_a_blocker_is_rejected(self, company: Orchestrator) -> None:
        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.REVIEW:
                payload = default_payload(invocation, state, org)
                return _submit({**payload, "issues": [{"severity": "blocker", "description": "It's wrong"}]})
            return None

        result, _ = _run(company, reply)
        assert result.status != "done"

    def test_sending_work_back_needs_a_reason(self, company: Orchestrator) -> None:
        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.REVIEW:
                return _submit({**default_payload(invocation, state, org), "decision": "changes_requested"})
            return None

        result, _ = _run(company, reply)
        assert result.status != "done"
        assert company.state(result.run_id).counters.rework_loops == 0

    def test_a_verdict_only_ever_applies_to_what_the_reviewer_was_shown(self, company: Orchestrator) -> None:
        """Code fills in what a review is about. A real reviewer mistyped the id six times; one naming another
        artifact on purpose can't move its verdict there either."""

        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.REVIEW:
                return _submit({**default_payload(invocation, state, org), "subject_artifact_id": "art-nope"})
            return None

        result, _ = _run(company, reply)
        state = company.state(result.run_id)
        verdicts = [a for a in state.artifacts.values() if a.type == "ReviewVerdict"]
        assert verdicts and all(v.payload["subject_artifact_id"] in state.artifacts for v in verdicts)
        assert all(v.payload["subject_artifact_id"] != "art-nope" for v in verdicts)

    def test_declaring_no_flags_doesnt_avoid_legal(self, company: Orchestrator) -> None:
        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.PLAN:
                return _submit(
                    {
                        "activated_departments": [{"id": "marketing", "reason": "r"}],
                        "work_packages": [
                            {"id": "wp", "title": "t", "owner_department": "marketing", "acceptance": ["a"]}
                        ],
                    }
                )
            if invocation.purpose == Purpose.EXECUTE:
                return _submit(default_payload(invocation, state, org), flags={"public_facing": False})
            return None

        result, client = _run(company, reply)
        assert result.status == "done"
        assert "legal/counsel" in [c["role"] for c in client.calls]


class TestEndlessBehaviour:
    def test_a_model_that_never_submits_stops(self, company: Orchestrator) -> None:
        result, client = _run(company, lambda *_: "I'd rather just chat about it.")
        assert _stopped_cleanly(result) and _no_final(company, result)
        assert len(client.calls) < 60

    def test_a_model_that_only_calls_tools_stops(self, company: Orchestrator) -> None:
        result, client = _run(company, lambda *_: [ToolCall(id="c1", name="memory_recall", arguments={"query": "x"})])
        assert _stopped_cleanly(result) and _no_final(company, result)
        assert len(client.calls) < 60

    def test_a_reviewer_that_never_approves_stops(self, company: Orchestrator) -> None:
        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.REVIEW:
                issues = [{"severity": "major", "description": "Not good enough"}]
                return _submit(
                    {**default_payload(invocation, state, org), "decision": "changes_requested", "issues": issues}
                )
            return None

        result, _ = _run(company, reply)
        assert _stopped_cleanly(result) and _no_final(company, result)
        assert company.state(result.run_id).counters.rework_loops < 20, "each round of rework has its limit"

    def test_a_model_that_always_escalates_reaches_a_person(self, company: Orchestrator) -> None:
        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.EXECUTE:
                return [ToolCall(id="c1", name=ESCALATE, arguments={"reason": "Not my job"})]
            if invocation.purpose == Purpose.ESCALATION:
                return _submit({"decision": "resume", "guidance": "Try again"})
            return None

        result, client = _run(company, reply)
        assert result.status == "blocked"
        assert len(client.calls) < 60

    def test_a_ceo_that_keeps_amending_the_plan_runs_out_of_amendments(self, company: Orchestrator) -> None:
        count = {"n": 0}

        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.EXECUTE:
                return [ToolCall(id="c1", name=ESCALATE, arguments={"reason": "Need more work added"})]
            if invocation.purpose == Purpose.ESCALATION:
                if invocation.role != org.root_role:
                    return _submit({"decision": "escalate_further", "guidance": "Up"})
                count["n"] += 1
                package = {
                    "id": f"extra{count['n']}",
                    "title": "More",
                    "owner_department": "engineering",
                    "acceptance": ["a"],
                }
                return _submit({"decision": "amend_plan", "added_work_packages": [package]})
            return None

        result, _ = _run(company, reply)
        state = company.state(result.run_id)
        assert state.counters.amendments <= 5
        assert result.status != "done"

    def test_a_run_cant_hand_out_work_forever(self, company: Orchestrator) -> None:
        """Every limit above could be fine, and a run could still go on. A person decides at this one."""
        run_id, _ = company.start("Goal")
        state = company.state(run_id)
        state.assignment_limit = state.counters.assignments + 1
        company.ledger.save(state, company._log(run_id).head, company.clock())
        ApiRunner(company, ScriptedClient(), ROUTES).resume(run_id)
        assert company.state(run_id).phase.value == "blocked"
        assert any(r.kind == "runaway" for r in company.state(run_id).human_requests.values())

    def test_every_command_has_a_limit_even_if_nobody_set_one(self, company: Orchestrator) -> None:
        assert company.org.pipeline.max_tokens_per_run == DEFAULT_MAX_TOKENS_PER_RUN
        assert company.org.pipeline.max_tokens_per_small_command == DEFAULT_MAX_TOKENS_PER_SMALL_COMMAND

    def test_a_company_with_no_budget_written_down_has_no_token_limit(self, tmp_path: Path) -> None:
        install_org(tmp_path / "bare", "Bare", available_packs())
        path = tmp_path / "bare" / "org.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["pipeline"].pop("budget", None)
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        assert load_org(tmp_path / "bare").pipeline.max_tokens_per_run is None

    def test_a_small_command_stops_at_its_own_smaller_limit(self, company: Orchestrator) -> None:
        """A stuck small command once spent 212,016 tokens. It stops well before that now, and says what happened."""

        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.INTAKE:
                small = {"size": "small", "route_department": "marketing", "route_role": "marketing/content-writer"}
                return _submit({**default_payload(invocation, state, org), **small})
            return None

        class Expensive(BadModel):
            def complete(self, request: ChatRequest) -> ChatResponse:
                response = super().complete(request)
                response.input_tokens = 30_000
                return response

        result = ApiRunner(company, Expensive(reply), ROUTES, max_turns=8).run("Write a short note")
        state = company.state(result.run_id)
        assert result.status == "blocked"
        assert state.size == "small"
        assert state.usage.total < 2 * DEFAULT_MAX_TOKENS_PER_SMALL_COMMAND
        question = next(iter(state.human_requests.values())).question
        assert f"over its limit of {DEFAULT_MAX_TOKENS_PER_SMALL_COMMAND}" in question and "assignments" in question

    def test_a_large_command_stops_at_the_first_call_over_its_limit(self, company: Orchestrator) -> None:
        """It may cross the limit by one call's worth, and no more: the next call never happens."""
        per_call = DEFAULT_MAX_TOKENS_PER_RUN // 5

        class Expensive(BadModel):
            def complete(self, request: ChatRequest) -> ChatResponse:
                response = super().complete(request)
                response.input_tokens = per_call
                return response

        result = ApiRunner(company, Expensive(lambda *_: None), ROUTES, max_turns=8).run("A big piece of work")
        state = company.state(result.run_id)
        assert result.status == "blocked"
        assert state.size == "large"
        assert DEFAULT_MAX_TOKENS_PER_RUN < state.usage.total <= DEFAULT_MAX_TOKENS_PER_RUN + per_call + 1_000

    def test_a_blocked_command_spends_nothing_more(self, company: Orchestrator) -> None:
        class Expensive(BadModel):
            def complete(self, request: ChatRequest) -> ChatResponse:
                response = super().complete(request)
                response.input_tokens = DEFAULT_MAX_TOKENS_PER_RUN
                return response

        client = Expensive(lambda *_: None)
        result = ApiRunner(company, client, ROUTES, max_turns=8).run("Goal")
        assert result.status == "blocked" and len(client.calls) == 1
        ApiRunner(company, client, ROUTES, max_turns=8).resume(result.run_id)
        assert len(client.calls) == 1, "resuming doesn't spend until a person decides"

    def test_a_person_can_raise_the_limit_for_one_command(self, company: Orchestrator) -> None:
        run_id, _ = company.start("Goal")
        _limit_tokens(company, run_id, 500_000)
        assert _token_limit(company.state(run_id), company.org) == 500_000

    def test_a_token_limit_stops_a_run(self, company: Orchestrator) -> None:
        run_id, _ = company.start("Goal")
        _limit_tokens(company, run_id, 1)
        client = BadModel(lambda *_: None)
        result = ApiRunner(company, client, ROUTES).resume(run_id)
        assert result.status == "blocked"
        assert len(client.calls) == 1


class TestACoordinatorCantDoARolesWork:
    """A real session once did every role's work itself, with one subagent, through an all-powerful server."""

    def _started(self, company: Orchestrator) -> tuple[McpService, str, str]:
        service = McpService(company, coordinator=True)
        started = service.run_start("Goal")
        return service, started["run_id"], started["actions"][0]["invocation_id"]

    def test_it_can_start_and_follow_work(self, company: Orchestrator) -> None:
        service, run_id, _ = self._started(company)
        assert service.run_status(run_id)["goal"] == "Goal"
        assert service.org_get()["identity"] == "coordinator"

    def test_it_only_sees_who_is_next(self, company: Orchestrator) -> None:
        service, run_id, _ = self._started(company)
        action = service.run_next_action(run_id)["actions"][0]
        assert action["role"] == company.org.root_role
        assert not {"assignment", "instructions", "output_schema"} & set(action)

    @pytest.mark.parametrize(
        ("tool", "arguments"),
        [
            ("artifact_submit", ({},)),
            ("review_submit", ({},)),
            ("handoff_request", ({},)),
            ("escalate", ("reason",)),
            ("usage_report", (1, 1)),
            ("memory_recall", ("query",)),
            ("memory_write", ("text",)),
            ("skill_search", ("query",)),
            ("skill_get", ("id",)),
            ("workspace_list", ()),
            ("workspace_read", ("a.md",)),
            ("workspace_write", ("a.md", "text")),
        ],
    )
    def test_everything_a_role_does_is_refused(self, company: Orchestrator, tool: str, arguments: tuple) -> None:
        service, run_id, invocation = self._started(company)
        before = company.state(run_id).counters.assignments
        with pytest.raises(ToolError):
            getattr(service, tool)(run_id, invocation, *arguments)
        state = company.state(run_id)
        assert state.counters.violations >= 1 or tool == "review_submit"
        assert state.counters.assignments == before and invocation in state.invocations

    def test_its_server_doesnt_even_list_them(self, company: Orchestrator) -> None:
        pytest.importorskip("mcp")
        import asyncio

        from try_professional.adapters.mcp import COORDINATOR_TOOLS
        from try_professional.adapters.mcp.server import build_server

        server = build_server(McpService(company, coordinator=True), coordinator=True)
        assert {tool.name for tool in asyncio.run(server.list_tools())} == set(COORDINATOR_TOOLS)

    def test_a_server_cant_be_both(self, company: Orchestrator) -> None:
        with pytest.raises(ValueError, match="both"):
            McpService(company, bound_role=company.org.root_role, coordinator=True)


class TestOverMcp:
    """A client can claim anything, so these hold on the server's side."""

    def _service(self, company: Orchestrator, role: str) -> tuple[McpService, str]:
        run_id, _ = company.start("Goal")
        return McpService(company, bound_role=role), run_id

    def test_a_bound_role_cant_act_for_another(self, company: Orchestrator) -> None:
        service, run_id = self._service(company, "marketing/content-writer")
        invocation = next(iter(company.state(run_id).invocations))
        with pytest.raises(ToolError, match="This server acts as"):
            service.artifact_submit(run_id, invocation, {})
        assert company.state(run_id).counters.violations == 1

    def test_a_bound_role_cant_read_the_whole_picture(self, company: Orchestrator) -> None:
        service, run_id = self._service(company, "marketing/content-writer")
        with pytest.raises(ToolError, match="operator"):
            service.audit_tail(run_id)
        status = service.run_status(run_id)
        assert "counters" not in status and "activated_departments" not in status and status["tasks"] == []
        assert service.run_next_action(run_id)["actions"] == [], "the CEO's work isn't shown to it"

    def test_tokens_a_client_reports_dont_decide_budgets(self, company: Orchestrator) -> None:
        run_id, _ = company.start("Goal")
        service = McpService(company)
        invocation_id = next(iter(company.state(run_id).invocations))
        service.usage_report(run_id, invocation_id, 0, 0)
        before = company.state(run_id).usage.total
        state = company.state(run_id)
        payload = default_payload(state.invocations[invocation_id], state, company.org)
        service.artifact_submit(run_id, invocation_id, payload)
        status = service.run_status(run_id)
        assert company.state(run_id).usage.total > before, "the server counted what passed through it"
        assert status["tokens_self_reported"] == 0

    def test_a_token_limit_applies_whatever_the_client_reports(self, company: Orchestrator, org: Org) -> None:
        run_id, _ = company.start("Goal")
        _limit_tokens(company, run_id, 1)
        service = McpService(company)
        invocation_id = next(iter(company.state(run_id).invocations))
        state = company.state(run_id)
        payload = default_payload(state.invocations[invocation_id], state, org)
        answer = service.artifact_submit(run_id, invocation_id, payload)
        assert answer["status"] == "blocked"
        assert company.state(run_id).phase.value == "blocked"


class TestSharedState:
    def test_the_state_lock_is_reentrant_and_exclusive(self, tmp_path: Path) -> None:
        first, second = StateLock(tmp_path), StateLock(tmp_path)
        with first, first:
            import try_professional.core.lock as lock

            original = lock.STATE_LOCK_SECONDS
            lock.STATE_LOCK_SECONDS = 0  # type: ignore[assignment]
            try:
                with pytest.raises(TimeoutError), second:
                    pass
            finally:
                lock.STATE_LOCK_SECONDS = original  # type: ignore[assignment]
        with second:
            pass

    def test_two_servers_keep_one_audit_chain(self, org: Org, tmp_path: Path) -> None:
        one = Orchestrator(org, tmp_path / "shared")
        two = Orchestrator(org, tmp_path / "shared")
        run_id, _ = one.start("Goal")
        invocation_id = next(iter(one.state(run_id).invocations))
        state = one.state(run_id)
        payload = default_payload(state.invocations[invocation_id], state, org)
        two.handle(run_id, Submission(invocation_id=invocation_id, actor=org.root_role, payload=payload))
        one.record(run_id, "note", {"from": "the first server"})
        assert one.verify_audit(run_id).ok, one.verify_audit(run_id).problem
        one.close()
        two.close()
