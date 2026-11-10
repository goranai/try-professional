"""A company that grows: the CEO proposes a role or department it lacks, from evidence, and a person decides.

From a real company's first day. It had a Validator and a Poster, and six hunts in a row went to Research alone: the
CEO sized each one small. Its reviewer sent the same work back again and again for a check only the Validator could
make, until a person stopped the run, and a person posted every finding by hand.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest

from try_professional import Company, CompanyError
from try_professional.adapters.api_runner.prompts import user_prompt
from try_professional.adapters.api_runner.runner import ApiRunner, ModelRoutes
from try_professional.adapters.api_runner.scripted import ScriptedClient, default_payload
from try_professional.adapters.mcp.claude_code import write_claude_code_files
from try_professional.core.orchestrator import Orchestrator
from try_professional.core.pipeline import Purpose, Submission
from try_professional.core.pipeline.state import Invocation
from try_professional.org import Org, load_org
from try_professional.packs import available_packs, install_org

ROUTES = ModelRoutes({"standard": "m", "executive": "m", "fast": "m"})
VIDEO_EDITOR = {
    "kind": "add_role",
    "department": "marketing",
    "role_id": "Video Editor",
    "title": "Video Editor",
    "mission": "Cut and caption short videos.",
    "skill_needs": ["edit short videos"],
    "because": "Two commands asked for video edits, and the content writer sent them back as out of scope.",
}


@pytest.fixture
def org(tmp_path: Path) -> Org:
    install_org(tmp_path / "org", "Test", available_packs())
    return load_org(tmp_path / "org")


@pytest.fixture
def company(org: Org, tmp_path: Path) -> Iterator[Orchestrator]:
    orchestrator = Orchestrator(org, tmp_path / "home")
    yield orchestrator
    orchestrator.close()


def _run(orchestrator: Orchestrator, override: Any) -> tuple[Any, ScriptedClient]:
    client = ScriptedClient(override)
    return ApiRunner(orchestrator, client, ROUTES).run("Write a launch note"), client


def _intake(**fields: Any) -> Any:
    def override(invocation: Any, state: Any, org_: Org) -> dict[str, Any] | None:
        if invocation.purpose == Purpose.INTAKE:
            return {**default_payload(invocation, state, org_), **fields}
        return None

    return override


class TestASmallCommandThatNeedsAnotherDepartment:
    SMALL: ClassVar[dict[str, str]] = {
        "size": "small",
        "route_department": "engineering",
        "route_role": "engineering/backend",
    }

    def test_its_criteria_naming_another_departments_role_get_it_planned(self, company: Orchestrator) -> None:
        criteria = ["The fix is built", "The analyst independently confirms the fix"]
        result, client = _run(company, _intake(**self.SMALL, success_criteria=criteria))
        assert result.status == "done"
        assert "plan" in [c["purpose"] for c in client.calls], "planning can bring in research"
        state = company.state(result.run_id)
        assert state.size == "large"
        charter = state.artifacts[state.charter_artifact_id or ""].payload
        assert any("Analyst (research/analyst)" in c and "plan work for each" in c for c in charter["constraints"])

    @pytest.mark.parametrize(
        "criterion",
        [
            "The tech lead signs it off",  # its own department
            "QA checks it",  # a reviewer, who runs anyway
            "The CEO is told",  # the root's department
            "The work is analysed",  # a word, not a role
        ],
    )
    def test_naming_its_own_department_or_a_reviewer_keeps_it_small(
        self, company: Orchestrator, criterion: str
    ) -> None:
        result, client = _run(company, _intake(**self.SMALL, success_criteria=[criterion]))
        assert [c["purpose"] for c in client.calls] == ["intake", "execute", "review"]
        assert company.state(result.run_id).size == "small"


class TestTheRetrospective:
    def test_a_small_command_that_was_sent_back_gets_one(self, company: Orchestrator) -> None:
        sent_back: list[str] = []

        def override(invocation: Any, state: Any, org_: Org) -> dict[str, Any] | None:
            if invocation.purpose == Purpose.INTAKE:
                small = {"size": "small", "route_department": "marketing", "route_role": "marketing/content-writer"}
                return {**default_payload(invocation, state, org_), **small}
            if invocation.purpose == Purpose.REVIEW and not sent_back:
                sent_back.append(invocation.id)
                issue = {"severity": "major", "description": "Needs a video, and nobody here edits video"}
                return {**default_payload(invocation, state, org_), "decision": "changes_requested", "issues": [issue]}
            return None

        result, client = _run(company, override)
        assert result.status == "done"
        assert client.calls[-1]["purpose"] == "retrospective"

    def test_a_smooth_small_command_has_none(self, company: Orchestrator) -> None:
        small = {"size": "small", "route_department": "marketing", "route_role": "marketing/content-writer"}
        _, client = _run(company, _intake(**small))
        assert "retrospective" not in [c["purpose"] for c in client.calls]

    def test_it_sees_every_department_and_what_went_wrong(self, company: Orchestrator, org: Org) -> None:
        sent_back: list[str] = []

        def override(invocation: Any, state: Any, org_: Org) -> dict[str, Any] | None:
            if invocation.purpose == Purpose.REVIEW and not sent_back:
                sent_back.append(invocation.id)
                issue = {"severity": "major", "description": "Only a validator could confirm this"}
                return {**default_payload(invocation, state, org_), "decision": "changes_requested", "issues": [issue]}
            return None

        result, _ = _run(company, override)
        state = company.state(result.run_id)
        retro = Invocation(id="x", role=org.root_role, purpose=Purpose.RETROSPECTIVE, output_type="Retrospective")
        prompt = user_prompt(retro, state, org, briefing=company.briefing(result.run_id, retro))
        assert "Every department and role now" in prompt
        assert "marketing (" in prompt and "content-writer" in prompt
        assert "was sent back 1 time(s). Last: qa requested changes: Only a validator could confirm this" in prompt
        assert '<untrusted kind="record">' in prompt, "reviewers' words are data"

    def test_departments_left_idle_across_commands_are_in_the_record(self, company: Orchestrator, org: Org) -> None:
        for _ in range(2):
            _run(company, None)
        result, _ = _run(company, None)
        retro = Invocation(id="x", role=org.root_role, purpose=Purpose.RETROSPECTIVE, output_type="Retrospective")
        lines = company.briefing(result.run_id, retro).org_record
        idle = next(line for line in lines if line.startswith("Departments given no work"))
        assert "marketing" in idle and "engineering" not in idle

    def test_the_playbook_says_when_to_propose_and_when_not_to(self) -> None:
        from try_professional import playbooks

        text = playbooks.section("ceo", "retrospective")
        assert "org_changes only on evidence" in text
        assert "Never propose a role that exists, or one for one-off work" in text
        assert "A department left idle isn't a missing role" in text
        assert "propose the missing role in org_changes" in playbooks.section("ceo", "intake")


class TestAProposal:
    def _retrospective(self, *changes: dict[str, Any]) -> Any:
        def override(invocation: Any, state: Any, org_: Org) -> dict[str, Any] | None:
            if invocation.purpose == Purpose.RETROSPECTIVE:
                return {"lessons": [], "org_changes": list(changes)}
            return None

        return override

    def test_waits_for_a_person_and_changes_nothing_until_then(self, tmp_path: Path) -> None:
        company = Company.create(tmp_path / "acme", name="Acme")
        before = (company.root / "org" / "org.yaml").read_text(encoding="utf-8")
        result = company.run("Make a launch video", client=ScriptedClient(self._retrospective(VIDEO_EDITOR)))
        assert result.status == "done"
        assert (company.root / "org" / "org.yaml").read_text(encoding="utf-8") == before
        [proposal] = company.org_proposals()
        assert proposal.ref == "marketing/video-editor", "an id a person can type, whatever the model wrote"
        assert proposal.source_run == result.run_id
        assert "Because: Two commands asked for video edits" in proposal.line()

    def test_accepted_adds_the_role_to_its_department(self, tmp_path: Path) -> None:
        company = Company.create(tmp_path / "acme", name="Acme")
        company.run("Make a launch video", client=ScriptedClient(self._retrospective(VIDEO_EDITOR)))
        [proposal] = company.org_proposals()
        added = company.decide_org_change(proposal.id, True, by="owner")
        assert added is not None and added.ref == "marketing/video-editor"
        role = company.org.role("marketing/video-editor")
        assert role.escalation == "marketing/marketing-lead" and role.skill_needs == ("edit short videos",)
        assert company.validate() == []
        assert company.org_proposals() == []
        with pytest.raises(CompanyError):
            company.decide_org_change(proposal.id, True, by="owner")

    def test_a_new_department_comes_with_its_head_and_the_same_review(self, tmp_path: Path) -> None:
        company = Company.create(tmp_path / "acme", name="Acme")
        change = {**VIDEO_EDITOR, "kind": "add_department", "department": "studio", "role_id": "studio-lead",
                  "title": "Studio Lead", "mission": "Run video production."}  # fmt: skip
        company.run("Make a launch video", client=ScriptedClient(self._retrospective(change)))
        [proposal] = company.org_proposals()
        added = company.decide_org_change(proposal.id, True, by="owner")
        assert added is not None and added.is_head
        org = company.org
        assert org.departments["studio"].head_ref == "studio/studio-lead"
        assert org.role("studio/studio-lead").escalation == org.root_role
        assert "TaskPlan" in org.role("studio/studio-lead").outputs, "a head assigns its department's work"
        assert company.validate() == []

    def test_rejected_is_never_asked_again(self, tmp_path: Path) -> None:
        company = Company.create(tmp_path / "acme", name="Acme")
        retro = ScriptedClient(self._retrospective(VIDEO_EDITOR))
        company.run("Make a launch video", client=retro)
        [proposal] = company.org_proposals()
        assert company.decide_org_change(proposal.id, False, by="owner") is None
        company.run("Make another launch video", client=ScriptedClient(self._retrospective(VIDEO_EDITOR)))
        assert company.org_proposals() == []
        assert not company.org.has_role("marketing/video-editor")

    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            ({**VIDEO_EDITOR, "department": "studio"}, "add_role needs an existing department"),
            ({**VIDEO_EDITOR, "role_id": "content-writer"}, "already exists"),
            ({**VIDEO_EDITOR, "kind": "add_department"}, "Department 'marketing' already exists"),
        ],
    )
    def test_one_that_cant_be_added_is_sent_back(
        self, company: Orchestrator, org: Org, change: dict[str, Any], reason: str
    ) -> None:
        run_id, actions = company.start("Do a thing")
        invocation_id = actions[0].invocation_id
        state = company.state(run_id)
        payload = {**default_payload(state.invocations[invocation_id], state, org), "org_changes": [change]}
        company.handle(run_id, Submission(invocation_id=invocation_id, actor=org.root_role, payload=payload))
        state = company.state(run_id)
        assert state.charter_artifact_id is None
        assert reason in state.invocations[invocation_id].feedback[-1]

    def test_made_at_intake_is_kept_even_if_the_run_stops(self, company: Orchestrator, org: Org) -> None:
        run_id, actions = company.start("Edit our launch video")
        invocation_id = actions[0].invocation_id
        state = company.state(run_id)
        payload = {**default_payload(state.invocations[invocation_id], state, org), "new_roles": [VIDEO_EDITOR]}
        company.handle(run_id, Submission(invocation_id=invocation_id, actor=org.root_role, payload=payload))
        company.cancel(run_id)
        assert [p.ref for p in company.company.org_proposals()] == ["marketing/video-editor"]

    def test_the_coordinator_is_told_to_show_it_to_the_person(self, tmp_path: Path) -> None:
        from try_professional.adapters.mcp import McpService

        company = Company.create(tmp_path / "acme", name="Acme")
        result = company.run("Make a launch video", client=ScriptedClient(self._retrospective(VIDEO_EDITOR)))
        orchestrator = company._orchestrator()
        try:
            answer = McpService(orchestrator, coordinator=True).run_next_action(result.run_id)
        finally:
            orchestrator.close()
        assert answer["status"] == "done"
        assert "marketing/video-editor" in answer["company_changes"][0]
        assert "trypro org review" in answer["company_changes"][-1]


class TestClaudeCodeAfterTheCompanyChanges:
    def test_each_role_keeps_the_model_a_person_gave_it(self, tmp_path: Path) -> None:
        company = Company.create(tmp_path / "acme", name="Acme")
        company.setup_claude_code()
        agent = company.root / ".claude" / "agents" / "trypro-research-analyst.md"
        agent.write_text(agent.read_text(encoding="utf-8").replace("model: sonnet", "model: opus"), encoding="utf-8")
        company.add_role("marketing", "video-editor", title="Video Editor", mission="Cut videos.")
        write_claude_code_files(company.root, company.org)
        assert "model: opus" in agent.read_text(encoding="utf-8")
        assert (company.root / ".claude" / "agents" / "trypro-marketing-video-editor.md").is_file()
        write_claude_code_files(company.root, company.org, model="haiku")
        assert "model: haiku" in agent.read_text(encoding="utf-8"), "a model named for all still applies to all"

    def test_only_a_person_decides_from_claude_code(self, tmp_path: Path) -> None:
        import json

        company = Company.create(tmp_path / "acme", name="Acme")
        company.setup_claude_code()
        deny = json.loads((company.root / ".claude" / "settings.json").read_text(encoding="utf-8"))["permissions"][
            "deny"
        ]
        assert "Bash(trypro org review *)" in deny and "PowerShell(trypro org decide *)" in deny

    def test_the_front_desk_records_what_it_posts_itself(self, tmp_path: Path) -> None:
        company = Company.create(tmp_path / "acme", name="Acme")
        company.setup_claude_code()
        front_desk = (company.root / "CLAUDE.md").read_text(encoding="utf-8")
        assert "trypro outcomes add <link> --run <run_id> --role" in front_desk
        command = (company.root / ".claude" / "commands" / "company.md").read_text(encoding="utf-8")
        assert "company_changes" in command and "trypro org review" in command

    def test_roles_are_told_the_machine_is_shared(self, tmp_path: Path) -> None:
        company = Company.create(tmp_path / "acme", name="Acme")
        company.setup_claude_code()
        agent = (company.root / ".claude" / "agents" / "trypro-research-analyst.md").read_text(encoding="utf-8")
        assert "at most 2 parallel workers and a time limit" in agent
