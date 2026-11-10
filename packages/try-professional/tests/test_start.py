"""`trypro start`, and the front desk: from saying what you want to a company at work."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from try_professional import Company, cli
from try_professional import setup as start_helpers
from try_professional.adapters.mcp.claude_code import FRONT_DESK_START, write_front_desk


@pytest.fixture
def company(tmp_path: Path) -> Company:
    return Company.create(tmp_path / "acme", name="Acme")


class TestFrontDesk:
    def test_plain_requests_go_to_the_company(self, company: Company) -> None:
        company.setup_claude_code()
        text = (company.root / "CLAUDE.md").read_text(encoding="utf-8")
        assert "Acme" in text
        assert ".claude/commands/company.md" in text
        assert "Only I decide: never choose for me" in text and "Claude Code asks me to confirm" in text

    def test_whats_already_in_claude_md_is_kept(self, company: Company) -> None:
        (company.root / "CLAUDE.md").write_text("# My notes\n\nKeep this.\n", encoding="utf-8")
        write_front_desk(company.root, company.org)
        write_front_desk(company.root, company.org)
        text = (company.root / "CLAUDE.md").read_text(encoding="utf-8")
        assert "Keep this." in text
        assert text.count(FRONT_DESK_START) == 1


class TestDesign:
    def _proposal(self, root: Path, status: str) -> None:
        folder = root / ".trypro" / "proposals" / "prop-abc123"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "meta.json").write_text(json.dumps({"status": status}), encoding="utf-8")

    def test_a_ready_design_is_returned(self, tmp_path: Path) -> None:
        def ask(root: Path, prompt: str) -> str:
            self._proposal(root, "proposed")
            return "prop-abc123"

        assert start_helpers.design_with_claude(tmp_path, "a launch plan for my bakery", print, ask) == "prop-abc123"

    def test_an_unfinished_design_is_carried_on(self, tmp_path: Path) -> None:
        prompts: list[str] = []

        def ask(root: Path, prompt: str) -> str:
            prompts.append(prompt)
            self._proposal(root, "judging" if len(prompts) == 1 else "proposed")
            return "prop-abc123"

        assert start_helpers.design_with_claude(tmp_path, "a bakery", print, ask) == "prop-abc123"
        assert "org_proposal_get" in prompts[1]

    def test_no_design_means_nothing(self, tmp_path: Path) -> None:
        assert start_helpers.design_with_claude(tmp_path, "x", print, lambda root, prompt: "I can't.") is None


class TestStart:
    def _args(self, root: Path, request: str) -> Any:
        return cli.build_parser().parse_args(["start", request, "--dir", str(root), "--no-launch"])

    def test_a_folder_with_a_company_gets_the_request(
        self, company: Company, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli, "setup_claude_code", lambda *a, **k: 0)
        monkeypatch.setattr(start_helpers, "borrow_github_token", lambda say: None)
        designed: list[str] = []
        monkeypatch.setattr(start_helpers, "design_with_claude", lambda *a, **k: designed.append("x"))
        args = self._args(company.root, "Write this week's newsletter")
        assert args.func(args) == 0
        assert not designed, "an existing company isn't designed again"
        assert (company.root / "CLAUDE.md").is_file()
        assert "Write this week's newsletter" in capsys.readouterr().out

    def test_a_failed_design_activates_nothing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli, "setup_claude_code", lambda *a, **k: 0)
        monkeypatch.setattr(start_helpers, "borrow_github_token", lambda say: None)
        monkeypatch.setattr(start_helpers, "design_with_claude", lambda *a, **k: None)
        args = self._args(tmp_path / "new", "a launch plan for my bakery")
        assert args.func(args) == 1
        assert not (tmp_path / "new" / "org" / "org.yaml").exists()

    def test_setup_problems_stop_it_first(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli, "setup_claude_code", lambda *a, **k: 2)
        monkeypatch.setattr(start_helpers, "borrow_github_token", lambda say: None)
        args = self._args(tmp_path / "new", "anything")
        assert args.func(args) == 2
