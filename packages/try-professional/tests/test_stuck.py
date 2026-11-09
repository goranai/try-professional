"""A run whose tokens rise while nothing moves is stuck, and more budget doesn't fix it.

A real run on a cheap model blocked at its limit, was given more, and blocked again with nothing done: the same role kept
rewriting its workspace and never handed anything in. These play that, and what a person can do about it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from try_professional.adapters.api_runner.clients import ChatRequest, ToolCall
from try_professional.adapters.api_runner.runner import ApiRunner
from try_professional.adapters.api_runner.scripted import default_payload
from try_professional.core.orchestrator import Orchestrator
from try_professional.core.pipeline import HumanDecided, IllegalEvent, Purpose
from try_professional.core.pipeline.machine import STUCK_CALLS, STUCK_REFUSALS
from try_professional.org import Org, load_org
from try_professional.packs import available_packs, install_org

from .conftest import with_limits
from .test_misbehaving_model import ROUTES, BadModel, _submit

SMALL = {"size": "small", "route_department": "marketing", "route_role": "marketing/content-writer"}


@pytest.fixture
def company(tmp_path: Path) -> Iterator[Orchestrator]:
    install_org(tmp_path / "org", "Test", available_packs())
    with_limits(tmp_path / "org")
    orchestrator = Orchestrator(load_org(tmp_path / "org"), tmp_path / "home")
    yield orchestrator
    orchestrator.close()


def _rewrites_forever(stuck: Purpose) -> Any:
    """A role that keeps writing files and never hands in its work."""

    def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
        if invocation.purpose == Purpose.INTAKE:
            return _submit({**default_payload(invocation, state, org), **SMALL})
        if invocation.purpose == stuck:
            return [ToolCall(id="c1", name="memory_recall", arguments={"query": "what did I write"})]
        return None

    return reply


def _run(company: Orchestrator, reply: Any, turns: int = 30) -> tuple[Any, BadModel]:
    client = BadModel(reply)
    return ApiRunner(company, client, ROUTES, max_turns=turns).run("Write a launch note"), client


def _question(company: Orchestrator, run_id: str) -> Any:
    return next(iter(company.state(run_id).human_requests.values()))


class TestNoticingIt:
    def test_a_role_that_never_hands_anything_in_stops_as_stuck(self, company: Orchestrator) -> None:
        result, client = _run(company, _rewrites_forever(Purpose.EXECUTE))
        assert result.status == "blocked"
        request = _question(company, result.run_id)
        assert request.kind == "stuck"
        assert "marketing/content-writer" in request.question
        assert "execute" in request.question
        assert request.options == ["retry", "cancel"]
        assert len(client.calls) <= STUCK_CALLS + 4

    def test_stuck_comes_before_a_budget_question(self, company: Orchestrator) -> None:
        """More budget would only buy more of the same loop."""

        class Expensive(BadModel):
            def complete(self, request: ChatRequest) -> Any:
                response = super().complete(request)
                response.input_tokens = 3_000
                return response

        result = ApiRunner(company, Expensive(_rewrites_forever(Purpose.EXECUTE)), ROUTES, max_turns=30).run("Note")
        assert result.status == "blocked"
        assert _question(company, result.run_id).kind == "stuck"

    def test_ordinary_work_is_never_called_stuck(self, company: Orchestrator) -> None:
        result, _ = _run(company, lambda *_: None)
        assert result.status == "done"
        assert company.state(result.run_id).calls_since_progress < STUCK_CALLS


class TestNoticingItSooner:
    """A real run looped for 197,000 tokens after a person added budget, because a manager's answer to an escalation,
    and the same work assigned again, counted as progress. docs/research/stuck has the replay."""

    def test_refused_hand_ins_count_as_trying_and_not_as_progress(self, company: Orchestrator) -> None:
        def refused(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.INTAKE:
                return _submit({**default_payload(invocation, state, org), **SMALL})
            if invocation.purpose == Purpose.EXECUTE:
                return _submit({"not": "a valid report"})
            return None

        result, client = _run(company, refused)
        request = _question(company, result.run_id)
        assert request.kind == "stuck"
        assert f"{STUCK_REFUSALS} hand-ins refused" in request.question
        assert len(client.calls) <= STUCK_REFUSALS + 2

    def test_added_budget_doesnt_buy_more_of_the_same_loop(self, company: Orchestrator) -> None:
        """Resuming with far more budget still stops a loop within one command's worth of tokens."""
        limit = company.org.pipeline.max_tokens_per_small_command
        per_call = limit // 8
        resumed = False

        class Expensive(BadModel):
            def complete(self, request: ChatRequest) -> Any:
                response = super().complete(request)
                response.input_tokens = per_call
                return response

        def answer(question: Any) -> Any:
            nonlocal resumed
            request = company.state(run_id).human_requests[question.request_id]
            if request.kind == "budget" and not resumed:
                resumed = True
                return HumanDecided(request_id=request.id, decision="resume", new_token_budget=limit * 50)
            return None

        def slow_then_loops(invocation: Any, state: Any, org: Org, request: Any) -> Any:
            if invocation.purpose == Purpose.INTAKE:
                return _submit({**default_payload(invocation, state, org), **SMALL})
            if invocation.purpose == Purpose.EXECUTE:
                return [ToolCall(id="c1", name="memory_recall", arguments={"query": "again"})]
            return None

        runner = ApiRunner(company, Expensive(slow_then_loops), ROUTES, max_turns=60, on_human=answer)
        run_id, _ = company.start("Note")
        result = runner.resume(run_id)
        state = company.state(result.run_id)
        assert _question(company, result.run_id).kind == "stuck"
        assert state.usage.total <= 2 * limit + per_call

    def test_an_escalation_answer_is_not_progress(self) -> None:
        from try_professional.core.artifacts.models import Artifact
        from try_professional.core.pipeline.budget import _progress_mark
        from try_professional.core.pipeline.state import RunState

        state = RunState(run_id="r", goal="g")
        before = _progress_mark(state)
        state.artifacts["a"] = Artifact(
            artifact_id="a", type="EscalationResolution", run_id="r", produced_by="lead", payload={}
        )
        state.counters.assignments += 1
        assert _progress_mark(state) == before
        state.artifacts["b"] = Artifact(artifact_id="b", type="Draft", run_id="r", produced_by="writer", payload={})
        assert _progress_mark(state) != before


class TestWorkOnAComputer:
    """A real role looking for a mistake in a repository ran out of turns, and was called stuck, while still working."""

    def _explores(self, commands: Any, hand_in_after: int) -> Any:
        count = {"calls": 0}

        def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.INTAKE:
                return _submit({**default_payload(invocation, state, org), **SMALL})
            if invocation.purpose == Purpose.EXECUTE and count["calls"] < hand_in_after:
                count["calls"] += 1
                return [ToolCall(id="c1", name="function=shell", arguments={"command": commands(count["calls"])})]
            return None

        return reply

    def test_new_commands_are_work_and_get_the_turns_they_need(self, company: Orchestrator) -> None:
        client = BadModel(self._explores(lambda n: f"echo step {n}", hand_in_after=STUCK_CALLS + 6))
        result = ApiRunner(company, client, ROUTES).run("Look through the repository")
        assert result.status == "done"
        audit = (company.audit_path(result.run_id)).read_text(encoding="utf-8")
        assert '"what": "echo step 3"' in audit, "what a role ran is on the record"
        assert "function=shell" not in audit, "a prefixed tool name is the same tool"

    def test_a_long_step_is_reminded_to_finish(self, company: Orchestrator) -> None:
        """A real worker given 40 turns spent them on tangents. At 15 and 30 it's told where it stands."""
        seen: list[str] = []

        class Watching(BadModel):
            def complete(self, request: ChatRequest) -> Any:
                last = request.messages[-1]
                if last.get("role") == "user" and "turns on this step" in str(last.get("content")):
                    seen.append(str(last["content"]))
                return super().complete(request)

        client = Watching(self._explores(lambda n: f"echo step {n}", hand_in_after=32))
        assert ApiRunner(company, client, ROUTES).run("Look through the repository").status == "done"
        assert [s.split(" turns")[0] for s in seen] == ["You've taken 15", "You've taken 30"]

    def test_a_tool_that_breaks_costs_one_call_not_the_command(
        self, company: Orchestrator, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import try_professional.adapters.api_runner.runner as runner_module

        real = runner_module.run_tool

        def breaks_once(context: Any, name: str, arguments: Any) -> str:
            if name == "shell" and arguments.get("command") == "echo step 1":
                raise RuntimeError("something nobody expected")
            return real(context, name, arguments)

        monkeypatch.setattr(runner_module, "run_tool", breaks_once)
        client = BadModel(self._explores(lambda n: f"echo step {n}", hand_in_after=3))
        assert ApiRunner(company, client, ROUTES).run("Look through the repository").status == "done"

    def test_the_same_command_again_and_again_is_still_stuck(self, company: Orchestrator) -> None:
        client = BadModel(self._explores(lambda n: "echo same", hand_in_after=100))
        result = ApiRunner(company, client, ROUTES).run("Look through the repository")
        assert result.status == "blocked"
        assert _question(company, result.run_id).kind == "stuck"


class TestASmallConversation:
    """A real analyst reading web pages sent 2.8 million input tokens in 22 calls: each page, again, every call."""

    def test_old_outputs_are_cut_in_batches_and_the_newest_kept(self) -> None:
        from try_professional.adapters.api_runner.runner import KEPT_TOOL_OUTPUTS, _cut_old_outputs

        page = "x" * 5000
        messages: list[Any] = [{"role": "user", "content": "Brief"}]
        for n in range(2 * KEPT_TOOL_OUTPUTS - 1):
            messages += [
                {"role": "assistant", "content": ""},
                {"role": "tool", "tool_call_id": str(n), "content": page},
            ]
        _cut_old_outputs(messages)
        assert all(m["content"] == page for m in messages if m["role"] == "tool"), "not yet: nothing changes"
        messages += [{"role": "tool", "tool_call_id": "last", "content": page}]
        _cut_old_outputs(messages)
        outputs = [m["content"] for m in messages if m["role"] == "tool"]
        assert all(len(o) < 600 and "cut to its start" in o for o in outputs[:-KEPT_TOOL_OUTPUTS])
        assert outputs[-KEPT_TOOL_OUTPUTS:] == [page] * KEPT_TOOL_OUTPUTS
        assert messages[0]["content"] == "Brief", "the brief is never cut"

    def test_the_same_for_claudes_message_shape(self) -> None:
        from try_professional.adapters.api_runner.runner import KEPT_TOOL_OUTPUTS, _cut_old_outputs

        blocks = [
            {"type": "tool_result", "tool_use_id": str(n), "content": "y" * 3000} for n in range(2 * KEPT_TOOL_OUTPUTS)
        ]
        messages: list[Any] = [{"role": "user", "content": blocks}]
        _cut_old_outputs(messages)
        assert sum("cut to its start" in b["content"] for b in blocks) == KEPT_TOOL_OUTPUTS


class TestRoomToReply:
    def test_a_reply_cut_off_gets_twice_the_room(self, company: Orchestrator) -> None:
        """A reasoning model's thinking counts as output. Cut off, it's asked again with more room, up to 128k."""
        from try_professional.adapters.api_runner.clients import ChatResponse

        rooms: list[int] = []

        class Cramped(BadModel):
            def complete(self, request: ChatRequest) -> Any:
                if request.metadata["invocation"].purpose == Purpose.EXECUTE:
                    rooms.append(request.max_tokens)
                    if request.max_tokens < 64_000:
                        return ChatResponse(text="thinking...", tool_calls=[], stop_reason="max_tokens",
                                            assistant_message={"role": "assistant", "content": "thinking..."})  # fmt: skip
                return super().complete(request)

        def small(invocation: Any, state: Any, org: Org, _: Any) -> Any:
            if invocation.purpose == Purpose.INTAKE:
                return _submit({**default_payload(invocation, state, org), **SMALL})
            return None

        result = ApiRunner(company, Cramped(small), ROUTES).run("Write a launch note")
        assert result.status == "done"
        assert rooms[:3] == [16_000, 32_000, 64_000]


class TestWhatAPersonCanDo:
    def _stuck(self, company: Orchestrator, purpose: Purpose = Purpose.EXECUTE) -> tuple[str, Any]:
        result, _ = _run(company, _rewrites_forever(purpose))
        assert result.status == "blocked"
        return result.run_id, _question(company, result.run_id)

    def test_retry_with_another_model_is_used_for_that_role(self, company: Orchestrator) -> None:
        run_id, request = self._stuck(company)
        company.handle(run_id, HumanDecided(request_id=request.id, decision="retry", model="a-better-model"))
        assert company.state(run_id).role_models == {"marketing/content-writer": "a-better-model"}
        seen: list[str] = []

        class Recording(BadModel):
            def complete(self, request: ChatRequest) -> Any:
                seen.append(request.model)
                return super().complete(request)

        result = ApiRunner(company, Recording(lambda *_: None), ROUTES).resume(run_id)
        assert result.status == "done"
        assert "a-better-model" in seen

    def test_cancel_stops_it_and_says_who(self, company: Orchestrator) -> None:
        run_id, request = self._stuck(company)
        company.handle(
            run_id, HumanDecided(request_id=request.id, decision="cancel", decided_by="Rina", note="Not worth it")
        )
        state = company.state(run_id)
        assert state.phase.value == "failed"
        assert "Rina" in (state.failure_reason or "")

    def test_a_stuck_review_can_be_waived_with_a_name_and_a_reason(self, company: Orchestrator) -> None:
        run_id, request = self._stuck(company, Purpose.REVIEW)
        assert request.options == ["retry", "waive", "cancel"]
        with pytest.raises(IllegalEvent):
            company.handle(run_id, HumanDecided(request_id=request.id, decision="waive", note="fine"))
        with pytest.raises(IllegalEvent):
            company.handle(run_id, HumanDecided(request_id=request.id, decision="waive", decided_by="Rina"))
        company.handle(
            run_id,
            HumanDecided(request_id=request.id, decision="waive", decided_by="Rina", note="I checked it myself"),
        )
        state = company.state(run_id)
        assert {
            "task_id": request.task_id,
            "gate": request.gate,
            "note": "I checked it myself",
            "decided_by": "Rina",
        } in (state.waivers)

    def test_work_that_is_itself_stuck_cannot_be_waived(self, company: Orchestrator) -> None:
        """There's nothing to approve, so the only ways on are another try or stopping."""
        run_id, request = self._stuck(company, Purpose.EXECUTE)
        with pytest.raises(IllegalEvent):
            company.handle(
                run_id, HumanDecided(request_id=request.id, decision="waive", decided_by="Rina", note="skip it")
            )


class TestDraftsWhenItStops:
    """A real stopped run left its user with nothing, though work had been made. Drafts hand it over, labelled."""

    @pytest.fixture
    def exporting(self, tmp_path: Path) -> Iterator[Orchestrator]:
        install_org(tmp_path / "org2", "Test", available_packs())
        orchestrator = Orchestrator(load_org(tmp_path / "org2"), tmp_path / "home2", deliverables_dir=tmp_path / "out")
        yield orchestrator
        orchestrator.close()

    def _never_approves(self, invocation: Any, state: Any, org: Org, _: Any) -> Any:
        if invocation.purpose == Purpose.INTAKE:
            return _submit({**default_payload(invocation, state, org), **SMALL})
        if invocation.purpose == Purpose.REVIEW:
            issues = [{"severity": "major", "description": "The price is wrong"}]
            return _submit(
                {**default_payload(invocation, state, org), "decision": "changes_requested", "issues": issues}
            )
        return None

    def test_unapproved_work_is_handed_over_and_labelled(self, exporting: Orchestrator, tmp_path: Path) -> None:
        result, _ = _run(exporting, self._never_approves)
        assert result.status == "blocked"
        draft = tmp_path / "out" / result.run_id / "draft"
        index = (draft / "index.md").read_text(encoding="utf-8")
        assert index.startswith("# NOT APPROVED: stopped at ")
        work = [p for p in draft.glob("*.md") if p.name != "index.md"]
        assert work, "the work it made is there"
        text = work[0].read_text(encoding="utf-8")
        assert text.startswith("# NOT APPROVED")
        assert "The price is wrong" in text and "changes_requested" in text

    def test_the_label_is_short_and_the_reason_is_whole(self, exporting: Orchestrator, tmp_path: Path) -> None:
        """A real label read "stopped at This command stopped at 63348 tokens…", cut mid-word."""
        result, _ = _run(exporting, self._never_approves)
        state = exporting.state(result.run_id)
        question = next(iter(state.human_requests.values())).question
        index = (tmp_path / "out" / result.run_id / "draft" / "index.md").read_text(encoding="utf-8")
        label = index.splitlines()[0]
        assert label.count("stopped") == 1 and len(label) < 80, label
        assert " ".join(question.split()) in index

    def test_a_run_that_finishes_after_all_replaces_the_drafts(self, exporting: Orchestrator, tmp_path: Path) -> None:
        result, _ = _run(exporting, self._never_approves)
        request = _question(exporting, result.run_id)
        exporting.handle(result.run_id, HumanDecided(request_id=request.id, decision="resume", note="Try again"))
        finished = ApiRunner(exporting, BadModel(lambda *_: None), ROUTES).resume(result.run_id)
        assert finished.status == "done"
        assert (tmp_path / "out" / result.run_id / "index.md").is_file()
        assert not (tmp_path / "out" / result.run_id / "draft").exists()

    def test_the_person_is_told_where_the_drafts_are(self, exporting: Orchestrator) -> None:
        from try_professional.adapters.mcp import McpService

        result, _ = _run(exporting, self._never_approves)
        message = McpService(exporting, coordinator=True).run_next_action(result.run_id)["needs_a_person"][0]
        assert "not approved" in message["tell_the_person"]
        assert "draft/index.md" in message["tell_the_person"]
