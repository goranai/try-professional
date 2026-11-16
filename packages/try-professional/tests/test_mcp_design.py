"""Designing a company over MCP with the client's own model, and setting one up for Claude Code."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from try_professional.adapters.mcp.claude_code import (
    ROLE_SERVER,
    SERVER,
    agent_name,
    role_agent,
    server_config,
    write_claude_code_files,
)
from try_professional.cli import main
from try_professional.generator import activate, load_proposal
from try_professional.generator.pull import PullError, begin_design, proposal_status, submit_design, submit_judgment
from try_professional.generator.templates import load_template
from try_professional.org import load_org
from try_professional.packs import available_packs, install_org
from try_professional.skills.rerank import RerankCache, record_judgment

from .test_memory import FakeEmbedder


def _design(template: str = "general-company") -> dict[str, Any]:
    proposal = load_template(template).keep_all("Test Co")
    payload: dict[str, Any] = proposal.model_dump(mode="json", exclude_defaults=True)
    return payload


@pytest.fixture
def embedder() -> Any:
    return FakeEmbedder()


class TestDesign:
    def test_it_hands_out_the_work_and_changes_nothing_outside_the_proposal(
        self, tmp_path: Path, embedder: Any
    ) -> None:
        action = begin_design(tmp_path, embedder, "A small bakery")
        assert action["status"] == "needs_design"
        assert action["submit_with"] == "org_proposal_submit"
        assert "A small bakery" in action["assignment"]
        assert action["output_schema"]["properties"]
        assert not (tmp_path / "org").exists()

    def test_the_same_checks_apply_as_with_a_model_called_here(self, tmp_path: Path, embedder: Any) -> None:
        action = begin_design(tmp_path, embedder, "A small bakery")
        answer = submit_design(tmp_path, embedder, action["proposal_id"], {"company_name": "X", "extra": 1})
        assert answer["status"] == "rejected"
        assert answer["reason"]
        assert answer["assignment"], "it says what to do again"

    def test_a_tool_or_gate_cant_be_added_through_a_design(self, tmp_path: Path, embedder: Any) -> None:
        action = begin_design(tmp_path, embedder, "A small bakery")
        payload = {**_design(action["template"]), "gates": {"qa": {"waive": True}}}
        assert submit_design(tmp_path, embedder, action["proposal_id"], payload)["status"] == "rejected"

    def test_retries_have_a_limit(self, tmp_path: Path, embedder: Any) -> None:
        action = begin_design(tmp_path, embedder, "A small bakery")
        proposal_id = action["proposal_id"]
        for _ in range(2):
            assert submit_design(tmp_path, embedder, proposal_id, {})["status"] == "rejected"
        assert submit_design(tmp_path, embedder, proposal_id, {})["status"] == "failed"
        with pytest.raises(PullError):
            submit_design(tmp_path, embedder, proposal_id, _design(action["template"]))

    def test_a_good_design_waits_for_a_person(self, tmp_path: Path, embedder: Any, monkeypatch: Any) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        action = begin_design(tmp_path, embedder, "A small bakery")
        answer = submit_design(tmp_path, embedder, action["proposal_id"], _design(action["template"]))
        assert answer["status"] == "ready_for_a_person"
        assert "trypro init --review" in answer["needs_a_person"]
        assert answer["review"]
        assert not (tmp_path / "org").exists(), "a client can't approve its own design"
        assert proposal_status(tmp_path, action["proposal_id"])["status"] == "ready_for_a_person"

    def test_once_a_person_approves_it_becomes_the_company(
        self, tmp_path: Path, embedder: Any, monkeypatch: Any
    ) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        action = begin_design(tmp_path, embedder, "A small bakery")
        submit_design(tmp_path, embedder, action["proposal_id"], _design(action["template"]))
        activate(load_proposal(tmp_path, action["proposal_id"]), tmp_path, embedder, by="a person")
        assert load_org(tmp_path / "org").company_name == "Test Co"

    def test_one_interrupted_while_finding_skills_says_so(self, tmp_path: Path, embedder: Any) -> None:
        """A process stopped at this point once left a proposal that crashed whoever asked about it."""
        action = begin_design(tmp_path, embedder, "A small bakery")
        path = tmp_path / ".trypro" / "proposals" / action["proposal_id"] / "meta.json"
        meta = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**meta, "status": "judging"}), encoding="utf-8")
        answer = proposal_status(tmp_path, action["proposal_id"])
        assert answer["status"] == "failed" and "org_propose" in answer["reason"]

    def test_an_unknown_proposal_is_refused(self, tmp_path: Path, embedder: Any) -> None:
        with pytest.raises(PullError, match="no proposal"):
            proposal_status(tmp_path, "prop-nope")
        with pytest.raises(PullError, match="no proposal"):
            submit_judgment(tmp_path, embedder, "prop-nope", "a/b", [])


class TestTheWholeWayAUserGoes:
    def test_from_a_clients_design_to_a_person_approving_to_role_agents(
        self, tmp_path: Path, embedder: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The README's steps. A folder with only a proposal in it has no trypro.yaml until a person approves."""
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setattr("try_professional.cli.FastEmbedEmbedder", FakeEmbedder)
        monkeypatch.setattr("try_professional.cli._person_at_terminal", lambda: True)
        action = begin_design(tmp_path, embedder, "A small bakery")
        submit_design(tmp_path, embedder, action["proposal_id"], _design(action["template"]))
        assert not (tmp_path / "trypro.yaml").exists()
        answers = iter(["a", "approve"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
        code = main(["init", "--review", action["proposal_id"], "--dir", str(tmp_path), "--by", "a person"])
        assert code == 0
        assert (tmp_path / "trypro.yaml").is_file() and load_org(tmp_path / "org").company_name == "Test Co"
        monkeypatch.chdir(tmp_path)
        assert main(["agents", "claude-code"]) == 0
        assert list((tmp_path / ".claude" / "agents").glob("trypro-*.md"))


class TestJudgments:
    def _role(self, tmp_path: Path) -> Any:
        install_org(tmp_path / "org", "Test", available_packs())
        return load_org(tmp_path / "org").role("engineering/backend")

    def test_only_a_clear_answer_about_a_shown_candidate_counts(self, tmp_path: Path) -> None:
        role = self._role(tmp_path)
        cache = RerankCache(tmp_path / "r.sqlite")
        candidates = [{"skill_id": "a/x", "content_sha256": "1"}, {"skill_id": "a/y", "content_sha256": "2"}]
        answer = [
            {"number": 1, "fit": True, "reason": "Fits"},
            {"number": 2, "fit": "maybe"},
            {"number": 9, "fit": True},
            "nonsense",
        ]
        assert record_judgment(role, candidates, answer, "m", "", cache) == 1
        assert cache.get(RerankCache.key_for(role, "a/x", "1", "m")) is not None
        assert cache.get(RerankCache.key_for(role, "a/y", "2", "m")) is None
        cache.close()


class TestClaudeCodeFiles:
    @pytest.fixture
    def org(self, tmp_path: Path) -> Any:
        install_org(tmp_path / "org", "Test", available_packs())
        return load_org(tmp_path / "org")

    def _front(self, text: str) -> dict[str, Any]:
        front: dict[str, Any] = yaml.safe_load(text.split("---")[1])
        return front

    def test_a_role_gets_only_its_own_bound_server(self, tmp_path: Path, org: Any) -> None:
        front = self._front(role_agent(tmp_path, org, org.role("engineering/backend")))
        assert front["name"] == agent_name("engineering/backend") == "trypro-engineering-backend"
        tools = [tool.strip() for tool in front["tools"].split(",")]
        # A full agent: its own role's server, and a computer to work with. Never the session's coordinator.
        assert tools[0] == f"mcp__{ROLE_SERVER}__*"
        assert {"Bash", "Read", "Write", "WebSearch"} <= set(tools)
        assert not any(tool.startswith(f"mcp__{SERVER}__") for tool in tools)
        args = front["mcpServers"][0][ROLE_SERVER]["args"]
        assert "--role" in args and "engineering/backend" in args

    def test_a_sandboxed_role_gets_only_its_server(self, tmp_path: Path, org: Any) -> None:
        front = self._front(role_agent(tmp_path, org, org.role("engineering/backend"), sandboxed=True))
        assert front["tools"] == f"mcp__{ROLE_SERVER}__*", "no shell, no file tools"

    def test_a_roles_server_isnt_named_like_the_sessions(self) -> None:
        """Claude Code used the session's server when they matched, so a role wasn't bound to anything."""
        assert ROLE_SERVER != SERVER

    def test_the_session_only_coordinates(self, tmp_path: Path) -> None:
        args = server_config(tmp_path)["args"]
        assert "--coordinator" in args and "--role" not in args

    def test_it_writes_one_agent_per_role_and_keeps_other_mcp_servers(self, tmp_path: Path, org: Any) -> None:
        (tmp_path / ".mcp.json").write_text('{"mcpServers": {"mine": {"command": "x"}}}', encoding="utf-8")
        write_claude_code_files(tmp_path, org)
        assert len(list((tmp_path / ".claude" / "agents").glob("trypro-*.md"))) == len(org.roles)
        assert (tmp_path / ".claude" / "commands" / "company.md").is_file()
        config = (tmp_path / ".mcp.json").read_text(encoding="utf-8")
        assert '"mine"' in config and '"trypro"' in config and "--coordinator" in config

    def test_it_allows_only_its_own_tools_and_keeps_other_settings(self, tmp_path: Path, org: Any) -> None:
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text('{"permissions": {"allow": ["Read"]}}', encoding="utf-8")
        write_claude_code_files(tmp_path, org)
        settings = yaml.safe_load((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
        assert settings["permissions"]["allow"] == ["Read", f"mcp__{SERVER}__*", f"mcp__{ROLE_SERVER}__*"]

    def test_the_command_never_answers_for_the_person(self, tmp_path: Path, org: Any) -> None:
        write_claude_code_files(tmp_path, org)
        text = (tmp_path / ".claude" / "commands" / "company.md").read_text(encoding="utf-8")
        assert "Never choose for me" in text and "known_limits" in text
        assert "When I've answered in my own words, record it with person_answer" in text

    def test_each_action_gets_a_fresh_subagent_even_for_the_same_role(self, tmp_path: Path, org: Any) -> None:
        write_claude_code_files(tmp_path, org)
        text = " ".join((tmp_path / ".claude/commands/company.md").read_text(encoding="utf-8").split())
        assert "For each action, start a fresh subagent" in text
        assert "Never resume or reuse a subagent, even when the next action has the same role" in text
        assert "Give it only the run_id" in text
        assert "start all of them at once, in one message" in text
        assert "one fresh subagent per action" in text

    def test_same_role_actions_wait_for_a_fresh_agent_and_select_the_first_open_assignment(
        self, tmp_path: Path, org: Any
    ) -> None:
        write_claude_code_files(tmp_path, org)
        text = " ".join((tmp_path / ".claude/commands/company.md").read_text(encoding="utf-8").split())
        assert "start only the first action for each role in the returned order" in text
        assert "Never run two subagents for the same role at once" in text
        assert "Wait for those subagents to stop, then call run_next_action" in text
        assert "use the refreshed actions" in text
        prompt = " ".join(role_agent(tmp_path, org, org.role("engineering/backend")).split())
        assert "If it returns several actions for your role, do only the first action in the returned order" in prompt
        assert "Submit or escalate that one assignment, then stop" in prompt

    def test_upgrading_an_unmarked_command_replaces_old_polling_and_keeps_personal_notes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("try_professional.cli.FastEmbedEmbedder", FakeEmbedder)
        assert main(["init", "--skip-model", "--dir", str(tmp_path)]) == 0
        path = tmp_path / ".claude/commands/company.md"
        historical = (Path(__file__).parent / "fixtures/claude-company-before-subagents.md").read_text(encoding="utf-8")
        note = "\n\nMy note: keep the weekly report short.\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(historical.rstrip() + note, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        assert main(["agents", "claude-code"]) == 0
        text = path.read_text(encoding="utf-8")
        assert text.count("Run this through the company: $ARGUMENTS") == 1
        assert "Call run_step with its run_id" not in text
        assert "one subagent per role" not in text
        assert "For each action, start a fresh subagent" in text
        assert "run_in_background" in text
        assert text.endswith(note)

    def test_long_runs_start_and_resume_in_the_background_without_chat_polling(self, tmp_path: Path, org: Any) -> None:
        write_claude_code_files(tmp_path, org)
        text = " ".join((tmp_path / ".claude/commands/company.md").read_text(encoding="utf-8").split())
        assert "For a long run, call your coordinator's run_in_background with the command as written" in text
        assert '"continue <run_id>" to carry a run on' in text
        assert "Tell me its run_id and that it's running, then return" in text
        assert "Only when I ask how it's going, call run_next_action" in text
        assert "Never start subagents or call run_step for a background run" in text
        assert 'resume a background run with run_in_background and "continue <run_id>"' in text

    def test_external_actions_keep_run_step_calls_in_fresh_coordinator_subagents(
        self, tmp_path: Path, org: Any
    ) -> None:
        write_claude_code_files(tmp_path, org)
        text = " ".join((tmp_path / ".claude/commands/company.md").read_text(encoding="utf-8").split())
        assert "start a fresh coordinator subagent with your trypro tools" in text
        assert "Give it only the run_id and invocation_id" in text
        assert "call run_step until it no longer says working" in text
        assert "Keep those run_step calls in that subagent" in text

    def test_person_decisions_and_posting_still_wait_for_the_person(self, tmp_path: Path, org: Any) -> None:
        write_claude_code_files(tmp_path, org)
        text = " ".join((tmp_path / ".claude/commands/company.md").read_text(encoding="utf-8").split())
        assert "show me each question under needs_a_person, word for word, with its options" in text
        assert "Never choose for me" in text
        assert "When I've answered in my own words, record it with person_answer" in text
        assert "company_changes (add this role? record with person_decide_company_change)" in text
        assert "company_memory (keep this for the whole company? record with person_decide_memory)" in text
        assert "Only record what I said" in text
        assert "Posting, sending, or opening anything outside waits for my approval" in text
        settings = json.loads((tmp_path / ".claude/settings.json").read_text(encoding="utf-8"))
        assert f"mcp__{SERVER}__person_*" in settings["permissions"]["ask"]
        assert f"mcp__{ROLE_SERVER}__person_*" in settings["permissions"]["deny"]
        rules = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "record it afterwards with outcome_record, naming the role" in rules
