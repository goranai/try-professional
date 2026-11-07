"""`trypro setup claude-code`, and approving a company in a folder it set up. Approval stays a person's."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from try_professional.cli import main
from try_professional.generator.provision import ProvisionResult, RoleMatch
from try_professional.generator.review import LOW_CONFIDENCE_SCORE, low_confidence, render
from try_professional.setup import runtime_of, setup_claude_code

from .test_generator import FakeEmbedder


@pytest.fixture
def folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("try_professional.setup._claude_version", lambda: "2.1.319")
    monkeypatch.setattr("try_professional.cli.FastEmbedEmbedder", lambda *a, **k: FakeEmbedder())
    return tmp_path


def _added(root: Path) -> tuple[bool, str]:
    return True, "Added the trypro server to Claude Code, for this folder."


class TestSetup:
    def test_it_adds_the_server_and_says_what_comes_next(self, folder: Path) -> None:
        said: list[str] = []
        assert setup_claude_code(folder, said.append, _added) == 0
        text = "\n".join(said)
        assert "Added the trypro server" in text
        assert "trypro init --review" in text and "/company" in text
        assert runtime_of(folder) == "claude-code"

    def test_a_missing_token_is_explained_and_doesnt_stop_it(self, folder: Path) -> None:
        said: list[str] = []
        assert setup_claude_code(folder, said.append, _added) == 0
        text = "\n".join(said)
        assert "gh auth token" in text
        assert "1. Set GITHUB_TOKEN" in text

    def test_with_a_token_there_is_one_step_fewer(self, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "planted-github-token-value")
        said: list[str] = []
        setup_claude_code(folder, said.append, _added)
        text = "\n".join(said)
        assert "Set GITHUB_TOKEN" not in text and "1. claude" in text
        assert "planted-github-token-value" not in text

    def test_without_claude_code_nothing_is_added(self, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("try_professional.setup._claude_version", lambda: None)
        calls: list[Path] = []

        def add(root: Path) -> tuple[bool, str]:
            calls.append(root)
            return True, ""

        assert setup_claude_code(folder, lambda text: None, add) == 2
        assert not calls
        assert runtime_of(folder) is None

    def test_a_failed_add_is_reported(self, folder: Path) -> None:
        said: list[str] = []
        assert setup_claude_code(folder, said.append, lambda root: (False, "Claude Code couldn't add it")) == 1
        assert "[!!] Claude Code couldn't add it" in said
        assert runtime_of(folder) is None

    def test_it_never_writes_a_key(self, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "planted-github-token-value")
        setup_claude_code(folder, lambda text: None, _added)
        for path in folder.rglob("*"):
            if path.is_file():
                assert "planted-github-token-value" not in path.read_text(encoding="utf-8", errors="replace")


class TestApprovingAfterSetup:
    def _approve(self, folder: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> str:
        monkeypatch.setattr("try_professional.cli._person_at_terminal", lambda: True)
        answers = iter(["a", "approve"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
        assert main(["init", "--template", "marketing-agency", "--no-discover"]) == 0
        output: str = capsys.readouterr().out
        return output

    def test_the_roles_are_written_for_claude_code(
        self, folder: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        setup_claude_code(folder, lambda text: None, _added)
        output = self._approve(folder, monkeypatch, capsys)
        assert list((folder / ".claude" / "agents").glob("trypro-*.md"))
        assert (folder / ".claude" / "commands" / "company.md").is_file()
        assert "/company" in output

    def test_without_setup_nothing_extra_is_written(
        self, folder: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        output = self._approve(folder, monkeypatch, capsys)
        assert not (folder / ".claude").exists()
        assert "trypro agents claude-code" in output

    def test_approval_still_needs_a_person(self, folder: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
        setup_claude_code(folder, lambda text: None, _added)
        monkeypatch.setattr("try_professional.cli._person_at_terminal", lambda: False)
        assert main(["init", "--template", "marketing-agency", "--no-discover"]) == 3
        assert not (folder / "org").exists()
        assert not (folder / ".claude").exists()


def _match(score: float, judged_by: str = "model") -> RoleMatch:
    return RoleMatch(
        skill_id="someone/skills/a-skill",
        name="a-skill",
        description="d",
        score=score,
        reason="Fits the role",
        category="testing",
        category_source="proposed",
        judged_by=judged_by,
        content_sha256="0" * 64,
    )


class TestLowConfidenceMatches:
    def test_a_weak_match_is_marked(self) -> None:
        assert low_confidence(_match(LOW_CONFIDENCE_SCORE - 0.01))
        assert not low_confidence(_match(LOW_CONFIDENCE_SCORE + 0.01))

    def test_a_match_no_model_judged_is_marked(self) -> None:
        assert low_confidence(_match(0.9, judged_by="embeddings"))

    def test_the_screen_marks_them_and_says_how_to_drop_one(self, folder: Path) -> None:
        from try_professional.generator import create_proposal

        bundle = create_proposal(folder, FakeEmbedder(), template_id="marketing-agency", discovery=False)
        ref = next(iter(bundle.org.roles))
        bundle.provisioned = ProvisionResult(matches={ref: [_match(0.31), _match(0.8)]})
        screen = render(bundle)
        assert "? someone/skills/a-skill  (0.31)  LOW CONFIDENCE" in screen
        assert "+ someone/skills/a-skill  (0.80)\n" in screen
        assert "1 of the matches above are marked ?" in screen
        assert "trypro skills reject" in screen
