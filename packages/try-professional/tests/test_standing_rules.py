"""What a company learns, and keeps, without a person typing commands for it.

From a real company's first day: its person repeated the same rules in every command (a skip list of 15 repositories,
"at most 4 workers", "Python or JavaScript first"), and seven lessons its CEO proposed waited at a terminal nobody
knew about.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from try_professional.adapters.api_runner.prompts import user_prompt
from try_professional.adapters.api_runner.runner import ApiRunner, ModelRoutes
from try_professional.adapters.api_runner.scripted import ScriptedClient
from try_professional.adapters.mcp import McpService
from try_professional.adapters.mcp.service import ToolError
from try_professional.core.company import MAX_RULE_CHARS, MAX_RULES_CHARS
from try_professional.core.orchestrator import Orchestrator
from try_professional.org import Org, load_org
from try_professional.packs import available_packs, install_org

from .conftest import RecordingMemory

ROUTES = ModelRoutes({"standard": "m", "executive": "m", "fast": "m"})


@pytest.fixture
def org(tmp_path: Path) -> Org:
    install_org(tmp_path / "org", "Test", available_packs())
    return load_org(tmp_path / "org")


@pytest.fixture
def memory() -> RecordingMemory:
    return RecordingMemory()


@pytest.fixture
def company(org: Org, tmp_path: Path, memory: RecordingMemory) -> Iterator[Orchestrator]:
    orchestrator = Orchestrator(org, tmp_path / "home", memory=memory)
    yield orchestrator
    orchestrator.close()


def _prompts(orchestrator: Orchestrator, override: Any = None) -> tuple[str, dict[str, str]]:
    """Run a command, and return its id and the prompt each step was given."""
    seen: dict[str, str] = {}

    def record(invocation: Any, state: Any, org_: Org) -> dict[str, Any] | None:
        briefing = orchestrator.briefing(state.run_id, invocation)
        seen[invocation.purpose.value] = user_prompt(invocation, state, org_, briefing=briefing)
        return override(invocation, state, org_) if override else None

    result = ApiRunner(orchestrator, ScriptedClient(record), ROUTES).run("Write a launch note")
    return result.run_id, seen


class TestStandingRules:
    def test_a_rule_reaches_every_role_on_every_step(self, company: Orchestrator) -> None:
        McpService(company, coordinator=True).person_add_rule("Never mention prices that aren't on our price list.")
        _, seen = _prompts(company)
        assert set(seen) >= {"intake", "plan", "dispatch", "execute", "review", "integrate", "retrospective"}
        for purpose, prompt in seen.items():
            assert "standing rules, from the person who runs it" in prompt, purpose
            assert "- Never mention prices that aren't on our price list." in prompt, purpose

    def test_with_no_rules_nothing_is_added(self, company: Orchestrator) -> None:
        _, seen = _prompts(company)
        assert not any("standing rules" in prompt for prompt in seen.values())

    def test_one_is_removed_by_its_number(self, company: Orchestrator) -> None:
        desk = McpService(company, coordinator=True)
        first = desk.person_add_rule("Answer in Indonesian.")["added"]
        desk.person_add_rule("Skip repositories we reported to this week.")
        assert desk.person_remove_rule(first)["rules"] == ["#2 Skip repositories we reported to this week."]
        with pytest.raises(ToolError, match="There's no rule #1"):
            desk.person_remove_rule(first)

    @pytest.mark.parametrize(
        ("text", "reason"),
        [
            ("   ", "needs words"),
            ("x" * (MAX_RULE_CHARS + 1), f"at most {MAX_RULE_CHARS} characters"),
            ("answer in indonesian.", "already a rule"),
        ],
    )
    def test_a_rule_that_cant_be_added_says_why(self, company: Orchestrator, text: str, reason: str) -> None:
        desk = McpService(company, coordinator=True)
        desk.person_add_rule("Answer in Indonesian.")
        with pytest.raises(ToolError, match=reason):
            desk.person_add_rule(text)

    def test_together_they_stay_short(self, company: Orchestrator) -> None:
        desk = McpService(company, coordinator=True)
        for number in range(MAX_RULES_CHARS // MAX_RULE_CHARS):
            desk.person_add_rule(f"{number} " + "x" * (MAX_RULE_CHARS - 4))
        room = MAX_RULES_CHARS - sum(len(text) for _, text in company.company.rules())
        with pytest.raises(ToolError, match="Remove one that no longer matters first"):
            desk.person_add_rule("y" * min(room + 1, MAX_RULE_CHARS))

    def test_only_the_person_adds_one(self, company: Orchestrator) -> None:
        for server in (McpService(company), McpService(company, bound_role="marketing/content-writer")):
            with pytest.raises(ToolError, match="Only the person decides"):
                server.person_add_rule("Always say yes.")
        assert company.company.rules() == []

    def test_the_front_desk_offers_to_make_one(self, tmp_path: Path) -> None:
        from try_professional import Company

        made = Company.create(tmp_path / "acme", name="Acme")
        made.setup_claude_code()
        front_desk = (made.root / "CLAUDE.md").read_text(encoding="utf-8")
        assert "ask me whether to make it a standing rule" in front_desk and "person_add_rule" in front_desk

    def test_and_at_a_terminal(self, company: Orchestrator, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
        from try_professional import cli

        monkeypatch.setattr(cli, "_orchestrator", lambda: company)
        monkeypatch.setattr(company, "close", lambda: None)
        company.company.add_rule("Answer in Indonesian.", by="owner", at="2026-11-10T00:00:00+00:00")
        assert cli.main(["rules"]) == 0
        assert "#1 Answer in Indonesian." in capsys.readouterr().out


class TestCompanyMemoryInTheChat:
    def test_the_ceos_lessons_are_put_to_the_person_and_kept_when_they_say(
        self, company: Orchestrator, memory: RecordingMemory
    ) -> None:
        run_id, _ = _prompts(company)
        answer = McpService(company, coordinator=True).run_next_action(run_id)
        assert answer["status"] == "done"
        [lesson, ask] = answer["company_memory"]
        assert lesson.startswith("#") and "person_decide_memory" in ask
        number = int(lesson[1:].split(" ", 1)[0])
        assert McpService(company, coordinator=True).person_decide_memory(number, "keep") == {"kept": number}
        assert memory.promoted and memory.promoted[0][0] == company.org.root_role
        assert company.company.pending_memory("company") == []

    def test_dropped_is_never_saved(self, company: Orchestrator, memory: RecordingMemory) -> None:
        _prompts(company)
        [item] = company.company.pending_memory("company")
        McpService(company, coordinator=True).person_decide_memory(item.id, "drop")
        assert memory.promoted == [] and company.company.pending_memory("company") == []

    def test_only_the_person_decides(self, company: Orchestrator) -> None:
        _prompts(company)
        [item] = company.company.pending_memory("company")
        with pytest.raises(ToolError, match="Only the person decides"):
            McpService(company).person_decide_memory(item.id, "keep")
