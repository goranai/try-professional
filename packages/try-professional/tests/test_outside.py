"""What a company puts out, what comes of it, what waits for a person, and what runs on a timer.

Any company can use these, whatever its business: a role posts something, the company follows it, and every role that
worked on it learns from what happened. A reply becomes a follow-up command. With nobody there to approve a command
that changes something outside, it waits for a person instead of being refused.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from try_professional import Company
from try_professional.adapters.api_runner.clients import ToolCall
from try_professional.adapters.api_runner.prompts import user_prompt
from try_professional.adapters.api_runner.runner import ApiRunner
from try_professional.adapters.api_runner.scripted import ScriptedClient, default_payload
from try_professional.adapters.mcp import McpService, ToolError
from try_professional.adapters.mcp.claude_code import server_config
from try_professional.chat import Conversation
from try_professional.core.brief import Briefing
from try_professional.core.orchestrator import Orchestrator
from try_professional.core.outside import (
    OutsideStore,
    describe_every,
    describe_link,
    outcome_lesson,
    parse_every,
    urls_in,
)
from try_professional.core.pipeline import Purpose
from try_professional.core.ports import NullMemory
from try_professional.org import Org, load_org
from try_professional.outcomes import FOLLOW_UP_MARK, GhUnavailable, check, judge
from try_professional.packs import available_packs, install_org
from try_professional.schedule import install_entry, run_round

from .test_misbehaving_model import ROUTES, BadModel, _submit

SMALL = {"size": "small", "route_department": "marketing", "route_role": "marketing/content-writer"}
WRITER = "marketing/content-writer"
NOW = "2026-11-09T10:00:00.000+00:00"


class Remembering(NullMemory):
    def __init__(self) -> None:
        self.notes: list[tuple[str, str]] = []

    def write(self, role: str, text: str) -> None:
        self.notes.append((role, text))


@pytest.fixture
def org(tmp_path: Path) -> Org:
    install_org(tmp_path / "org", "Test", available_packs())
    return load_org(tmp_path / "org")


@pytest.fixture
def company(org: Org, tmp_path: Path) -> Iterator[Orchestrator]:
    orchestrator = Orchestrator(org, tmp_path / "home", memory=Remembering())
    yield orchestrator
    orchestrator.close()


def _posts_once(command: str) -> Any:
    """A writer that runs one outside command while working, then hands in its work as usual."""
    done: list[bool] = []

    def reply(invocation: Any, state: Any, org: Org, _: Any) -> Any:
        if invocation.purpose == Purpose.INTAKE:
            return _submit({**default_payload(invocation, state, org), **SMALL})
        if invocation.purpose == Purpose.EXECUTE and not done:
            done.append(True)
            return [ToolCall(id="c1", name="shell", arguments={"command": command})]
        return None

    return reply


# ---- the record -------------------------------------------------------------------------------------------------


class TestTheRecord:
    def test_a_link_is_tracked_once(self, tmp_path: Path) -> None:
        store = OutsideStore(tmp_path / "o.sqlite")
        first = store.track("https://github.com/a/b/issues/1", run_id="r", role="x/y", credited=["x/y"], kind="issue",
                            title="Typo", at=NOW)  # fmt: skip
        again = store.track("https://github.com/a/b/issues/1", run_id="r2", role="x/z", credited=[], kind="", title="",
                            at=NOW)  # fmt: skip
        assert first is not None and again is not None and first.id == again.id
        assert store.track("not a link", run_id="r", role="x/y", credited=[], kind="", title="", at=NOW) is None
        store.close()

    def test_a_status_changes_once(self, tmp_path: Path) -> None:
        store = OutsideStore(tmp_path / "o.sqlite")
        item = store.track("https://x.example/p/1", run_id="r", role="x/y", credited=["x/y"], kind="post", title="",
                           at=NOW)  # fmt: skip
        assert item is not None
        assert store.settle(item.id, "accepted", "liked", NOW) is not None
        assert store.settle(item.id, "accepted", "liked again", NOW) is None
        with pytest.raises(ValueError):
            store.settle(item.id, "great", "", NOW)
        assert store.record_lines()[0] == "1 tracked: 1 accepted."
        store.close()

    def test_links_and_titles_come_from_what_a_command_printed(self) -> None:
        printed = "Creating issue in a/b\n\nhttps://github.com/a/b/issues/42\n"
        assert urls_in(printed) == ["https://github.com/a/b/issues/42"]
        assert describe_link("https://github.com/a/b/issues/42", 'gh issue create --title "Fix the typo" --body x') == (
            "issue",
            "Fix the typo",
        )
        assert describe_link("https://github.com/a/b/pull/7")[0] == "pull request"

    def test_intervals(self) -> None:
        assert parse_every("1d") == 86400 and parse_every("6h") == 21600 and describe_every(604800) == "1w"
        for wrong in ("5m", "daily", "1y"):
            with pytest.raises(ValueError):
                parse_every(wrong)

    def test_a_missed_schedule_runs_once_not_many_times(self, tmp_path: Path) -> None:
        store = OutsideStore(tmp_path / "o.sqlite")
        store.add_schedule("daily", "Do the thing", 86400, "2026-11-01T00:00:00.000+00:00")
        assert [s.name for s in store.due(NOW)] == ["daily"]
        store.ran("daily", NOW, "run-1")
        assert store.due(NOW) == []
        assert store.schedules("daily")[0].next_at == "2026-11-10T10:00:00.000+00:00"
        with pytest.raises(ValueError):
            store.add_schedule("two words", "x", 86400, NOW)
        store.close()

    def test_a_held_command_is_decided_once(self, tmp_path: Path) -> None:
        store = OutsideStore(tmp_path / "o.sqlite")
        number = store.hold(run_id="r", role="x/y", command="git push", workspace=tmp_path, at=NOW)
        assert store.hold(run_id="r", role="x/y", command="git push", workspace=tmp_path, at=NOW) == number
        assert store.decide_held(number, "denied", by="Rina", at=NOW)
        assert not store.decide_held(number, "approved", by="Rina", at=NOW)
        assert store.held() == []
        store.close()


# ---- learning from it ----------------------------------------------------------------------------------------------


class TestLearning:
    def test_every_role_that_worked_on_it_learns(self, company: Orchestrator) -> None:
        result = ApiRunner(company, ScriptedClient(), ROUTES).run("Write a launch note")
        item = company.track_outcome(result.run_id, WRITER, "https://github.com/a/b/issues/9", title="Launch note")
        assert item is not None
        credited = set(item.credited)
        assert WRITER in credited and len(credited) >= 2, "the manager who assigned it shares the outcome"
        settled = company.settle_outcome(item.id, "duplicate", "closed as a duplicate of #3", by="a person")
        assert settled is not None
        notes = company.memory.notes  # type: ignore[attr-defined]
        assert {role for role, _ in notes} == credited
        assert "duplicate" in notes[0][1] and "#3" in notes[0][1]
        departments = {role.split("/")[0] for role in credited}
        for department in departments:
            pending = company.company.pending_memory("department", department)
            assert [p.text for p in pending] == [outcome_lesson(settled)]
        assert company.settle_outcome(item.id, "duplicate", "again", by="a person") is None, "a lesson is written once"

    def test_roles_see_the_track_record(self, company: Orchestrator) -> None:
        result = ApiRunner(company, ScriptedClient(), ROUTES).run("Write a launch note")
        item = company.track_outcome(result.run_id, WRITER, "https://github.com/a/b/issues/9")
        assert item is not None
        company.settle_outcome(item.id, "rejected", "not planned", by="a person")
        state = company.state(result.run_id)
        briefing = company.briefing(result.run_id, _fake_invocation())
        assert briefing.outside == company.outside.record_lines()
        prompt = user_prompt(_fake_invocation(), state, company.org, briefing=Briefing(outside=briefing.outside))
        assert "What happened to this company's earlier work outside" in prompt
        assert "rejected: issue https://github.com/a/b/issues/9" in prompt
        assert '<untrusted kind="outcomes">' in prompt


def _fake_invocation() -> Any:
    from try_professional.core.pipeline.state import Invocation

    return Invocation(id="i", role=WRITER, purpose=Purpose.EXECUTE, output_type="TaskReport")


# ---- the role's shell -----------------------------------------------------------------------------------------------


class TestTheShell:
    def test_with_nobody_there_an_outside_command_waits_for_a_person(self, company: Orchestrator) -> None:
        client = BadModel(_posts_once('gh issue create --title "Fix the typo" --body "x"'))
        result = ApiRunner(company, client, ROUTES, hold=True).run("Report the typo")
        assert result.status == "done", "the run carries on"
        waiting = company.outside.held()
        assert [(h.role, h.command) for h in waiting] == [(WRITER, 'gh issue create --title "Fix the typo" --body "x"')]

        def execute(workspace: Path, command: str) -> str:
            assert workspace == company.workspace(result.run_id)
            return "exit code 0\nhttps://github.com/a/b/issues/77\n"

        done = company.run_held(waiting[0].id, execute, by="Rina")
        assert done is not None and done.status == "approved" and done.decided_by == "Rina"
        tracked = company.outside.outcomes()
        assert [(t.url, t.kind, t.title, t.role) for t in tracked] == [
            ("https://github.com/a/b/issues/77", "issue", "Fix the typo", WRITER)
        ]
        assert company.run_held(waiting[0].id, execute, by="Rina") is None, "it can't run twice"

    def test_without_holding_it_is_still_refused(self, company: Orchestrator) -> None:
        client = BadModel(_posts_once("git push origin main"))
        ApiRunner(company, client, ROUTES).run("Push it")
        assert company.outside.held() == []

    def test_an_approved_command_is_followed(self, company: Orchestrator) -> None:
        command = 'echo gh issue create --title "Broken link" && echo https://github.com/a/b/issues/5'
        client = BadModel(_posts_once(command))
        ApiRunner(company, client, ROUTES, approve=lambda role, cmd: True).run("Report the link")
        assert [(t.url, t.title) for t in company.outside.outcomes()] == [
            ("https://github.com/a/b/issues/5", "Broken link")
        ]

    def test_reading_commands_arent_followed(self, company: Orchestrator) -> None:
        client = BadModel(_posts_once("echo https://github.com/a/b/issues/5"))
        ApiRunner(company, client, ROUTES).run("Look at it")
        assert company.outside.outcomes() == []


# ---- what roles are told about their computer -----------------------------------------------------------------------


class TestWhatRolesAreTold:
    def test_a_role_with_a_computer_is_told_so(self, org: Org) -> None:
        from try_professional.core.pipeline import initial_state

        state = initial_state("r", "Find a typo in a popular repository")
        on = user_prompt(_fake_invocation(), state, org, [], computer=True)
        off = user_prompt(_fake_invocation(), state, org, [], computer=False)
        assert "a shell (git, gh, curl, python) and the web" in on and "no repository or shell" not in on
        assert "no repository or shell" in off

    def test_planners_know_what_roles_can_do(self, org: Org) -> None:
        from try_professional.adapters.api_runner.prompts import _reach

        assert "search GitHub" in _reach(org, [], False, computer=True)
        assert "nothing can run a program" in _reach(org, [], False, computer=False)

    def test_a_sandboxed_role_server_says_so(self, tmp_path: Path) -> None:
        assert "--sandboxed" in server_config(tmp_path, "marketing/content-writer", sandboxed=True)["args"]
        assert "--sandboxed" not in server_config(tmp_path, "marketing/content-writer")["args"]
        assert "--sandboxed" not in server_config(tmp_path, sandboxed=True)["args"], "a coordinator has no tools"


# ---- checking GitHub -------------------------------------------------------------------------------------------------


def _comment(login: str, body: str) -> dict[str, Any]:
    return {"author": {"login": login}, "body": body}


class FakeGh:
    def __init__(self, views: dict[str, dict[str, Any]]) -> None:
        self.views = views
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[:2] == ["api", "user"]:
            return "our-bot\n"
        return json.dumps(self.views[args[2]])


class TestChecking:
    @pytest.mark.parametrize(
        ("kind", "data", "status"),
        [
            ("issue", {"state": "CLOSED", "stateReason": "COMPLETED"}, "accepted"),
            ("issue", {"state": "CLOSED", "stateReason": "NOT_PLANNED"}, "rejected"),
            ("issue", {"state": "CLOSED", "stateReason": "DUPLICATE"}, "duplicate"),
            ("issue", {"state": "CLOSED", "stateReason": "COMPLETED", "labels": [{"name": "invalid"}]}, "rejected"),
            ("issue", {"state": "CLOSED", "labels": [{"name": "duplicate"}]}, "duplicate"),
            ("pull", {"state": "MERGED"}, "accepted"),
            ("pull", {"state": "CLOSED"}, "rejected"),
            ("issue", {"state": "OPEN"}, "open"),
        ],
    )
    def test_githubs_own_record_decides(self, kind: str, data: dict[str, Any], status: str) -> None:
        assert judge(kind, data)[0] == status

    def _tracked(self, company: Orchestrator, url: str, at: str = NOW) -> int:
        result = ApiRunner(company, ScriptedClient(), ROUTES).run("Report it")
        company.clock = lambda: at
        item = company.track_outcome(result.run_id, WRITER, url, title="Typo in README")
        assert item is not None
        return item.id

    def test_a_reply_queues_one_follow_up(self, company: Orchestrator) -> None:
        url = "https://github.com/a/b/issues/3"
        self._tracked(company, url)
        gh = FakeGh({url: {"state": "OPEN", "comments": [_comment("our-bot", "Here's the report"),
                                                          _comment("dependabot[bot]", "noise"),
                                                          _comment("maintainer", "Which version?")]}})  # fmt: skip
        report = check(company, gh=gh, now=lambda: NOW)
        assert report.follow_ups == [url]
        queued = company.company.queued()
        assert len(queued) == 1 and queued[0]["goal"].startswith(FOLLOW_UP_MARK) and url in queued[0]["goal"]
        assert "1 new reply" in queued[0]["goal"]
        assert check(company, gh=gh, now=lambda: NOW).follow_ups == [], "the same reply isn't followed up twice"

    def test_a_close_settles_it_with_whats_said(self, company: Orchestrator) -> None:
        url = "https://github.com/a/b/issues/4"
        self._tracked(company, url)
        gh = FakeGh({url: {"state": "CLOSED", "stateReason": "NOT_PLANNED",
                           "comments": [_comment("maintainer", "Not a bug: that's how it's meant to work.")]}})  # fmt: skip
        report = check(company, gh=gh, now=lambda: NOW)
        assert [i.status for i in report.settled] == ["rejected"]
        assert "maintainer" in report.settled[0].note and "meant to work" in report.settled[0].note
        assert company.memory.notes, "the roles learn it"  # type: ignore[attr-defined]

    def test_a_maintainers_word_is_what_its_learned_from(self, company: Orchestrator) -> None:
        merged, closed = "https://github.com/a/b/pull/8", "https://github.com/a/b/issues/9"
        self._tracked(company, merged)
        self._tracked(company, closed)
        maintainer = {**_comment("owner", "We track this in #2."), "authorAssociation": "OWNER"}
        gh = FakeGh({
            merged: {"state": "MERGED", "comments": [_comment("passerby", "spam spam")]},
            closed: {"state": "CLOSED", "stateReason": "DUPLICATE", "comments": [maintainer, _comment("x", "+1")]},
        })  # fmt: skip
        notes = {item.url: item.note for item in check(company, gh=gh, now=lambda: NOW).settled}
        assert notes[merged] == "merged", "a passer-by's comment on accepted work teaches nothing"
        assert "owner" in notes[closed] and "#2" in notes[closed] and "+1" not in notes[closed]

    def test_no_answer_for_a_month_is_ignored(self, company: Orchestrator) -> None:
        url = "https://github.com/a/b/issues/5"
        self._tracked(company, url, at="2026-10-01T00:00:00.000+00:00")
        report = check(company, gh=FakeGh({url: {"state": "OPEN", "comments": []}}), now=lambda: NOW)
        assert [i.status for i in report.settled] == ["ignored"]

    def test_without_gh_it_says_so(self, company: Orchestrator) -> None:
        self._tracked(company, "https://github.com/a/b/issues/6")

        def missing(args: list[str]) -> str:
            raise GhUnavailable("gh isn't installed.")

        report = check(company, gh=missing, now=lambda: NOW)
        assert report.problems == ["gh isn't installed."] and report.checked == 0

    def test_other_links_are_left_to_a_person(self, company: Orchestrator) -> None:
        self._tracked(company, "https://x.example/posts/1")
        gh = FakeGh({})
        assert check(company, gh=gh, now=lambda: NOW).checked == 0 and gh.calls == []


# ---- a round on a timer ------------------------------------------------------------------------------------------------


class TestARound:
    @pytest.fixture
    def acme(self, tmp_path: Path) -> Company:
        return Company.create(tmp_path / "acme", name="Acme")

    def test_due_schedules_and_follow_ups_run_and_a_persons_queue_waits(self, acme: Company) -> None:
        orchestrator = acme._orchestrator()
        orchestrator.outside.add_schedule("daily", "Write a short status note", 86400, "2026-11-01T00:00:00.000+00:00")
        orchestrator.company.add_queued(f"{FOLLOW_UP_MARK} the issue at https://github.com/a/b/issues/1.", NOW)
        orchestrator.company.add_queued("Something a person queued for later", NOW)
        orchestrator.close()
        said: list[str] = []
        outcome = run_round(acme.root, say=said.append, dry_run=True, gh=FakeGh({}), now=lambda: NOW)
        assert [what for what, _, _ in outcome.ran] == ["daily", "follow-up"]
        assert all(status == "done" for _, _, status in outcome.ran)
        orchestrator = acme._orchestrator()
        try:
            assert [q["goal"] for q in orchestrator.company.queued()] == ["Something a person queued for later"]
            assert orchestrator.outside.due(NOW) == []
        finally:
            orchestrator.close()
        assert "daily: done" in outcome.summary("Acme")

    def test_nothing_due_is_said_plainly(self, acme: Company) -> None:
        outcome = run_round(acme.root, say=lambda _: None, dry_run=True, gh=FakeGh({}), now=lambda: NOW)
        assert outcome.summary("Acme") == "Acme: nothing was due."

    def test_the_systems_scheduler_entry_runs_a_round_in_this_folder(self, acme: Company) -> None:
        shown, _ = install_entry(acme.root, 30)
        assert "schedule run --dir" in shown and str(acme.root.resolve()) in shown


# ---- chat and Claude Code -----------------------------------------------------------------------------------------------


class TestFromChat:
    def test_a_held_command_can_be_approved_in_chat(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        acme = Company.create(tmp_path / "acme", name="Acme")
        orchestrator = acme._orchestrator()
        run_id, _ = orchestrator.start("Report it")
        number = orchestrator.hold_command(run_id, WRITER, "gh issue create --title x --body y")
        orchestrator.close()
        import try_professional.adapters.api_runner.tools as tools

        monkeypatch.setattr(tools, "execute", lambda workspace, command: "exit code 0\nhttps://github.com/a/b/issues/8")
        conversation = Conversation(acme.root, ask_person=lambda q, o: ("", ""), approve=None, person="telegram:1")
        assert f"#{number}" in conversation.handle("/held").text
        assert "Ran" in conversation.handle(f"/approve {number}").text
        assert "isn't waiting" in conversation.handle(f"/deny {number}").text
        assert "1 tracked: 1 open." in conversation.handle("/outcomes").text


class TestFromClaudeCode:
    def test_a_role_tracks_what_it_put_out(self, company: Orchestrator) -> None:
        run_id, _ = company.start("Write a launch note")
        coordinator = McpService(company, coordinator=True)
        coordinator.run_next_action(run_id)
        invocation = next(iter(company.state(run_id).invocations.values()))
        role = McpService(company, bound_role=invocation.role)
        answer = role.outcome_track(run_id, invocation.id, "https://github.com/a/b/pull/12", "Fix docs")
        assert answer["tracked"] is True and answer["kind"] == "pull request"
        with pytest.raises(ToolError):
            role.outcome_track(run_id, invocation.id, "a pull request I opened")
        with pytest.raises(ToolError):
            coordinator.outcome_track(run_id, invocation.id, "https://github.com/a/b/pull/13")
