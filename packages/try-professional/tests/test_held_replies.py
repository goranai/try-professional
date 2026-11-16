"""QA observed interrupted shell results and chat duplicates being reported as completed."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from try_professional import Company
from try_professional.adapters.api_runner import tools
from try_professional.chat import Conversation
from try_professional.cli import main
from try_professional.core.orchestrator import Execute, Orchestrator

NOW = "2026-11-16T00:00:00+00:00"


@pytest.fixture
def company(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Company:
    company = Company.create(tmp_path / "company", name="Test Co")
    monkeypatch.setattr("try_professional.cli._here", lambda _: company.root)
    monkeypatch.setattr("try_professional.cli._person_at_terminal", lambda: True)
    return company


def _hold(company: Company, command: str) -> int:
    orchestrator = company._orchestrator()
    try:
        return orchestrator.outside.hold(run_id="old", role="x/a", command=command, workspace=company.root, at=NOW)
    finally:
        orchestrator.close()


def _chat(company: Company) -> Conversation:
    return Conversation(company.root, ask_person=lambda q, o: ("", ""), approve=None, person="a person")


def _legacy_duplicate(company: Company, number: int) -> int:
    orchestrator = company._orchestrator()
    try:
        with sqlite3.connect(orchestrator.outside.path) as connection:
            cursor = connection.execute(
                "INSERT INTO held (run_id, role, command, workspace, created_at, status) "
                "SELECT run_id, role, command, workspace, created_at, 'waiting' FROM held WHERE id = ?",
                (number,),
            )
            return int(cursor.lastrowid or 0)
    finally:
        orchestrator.close()


@pytest.mark.parametrize("front_door", ["cli", "chat"])
@pytest.mark.parametrize("failure", ["killed", "nonzero", "timeout", "missing_command", "missing_shell"])
def test_failed_shell_approval_stays_unverified_and_is_not_repeated(
    company: Company, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], front_door: str, failure: str
) -> None:
    commands = {
        "killed": "printf sent >> count.txt; kill -KILL $$",
        "nonzero": "printf sent >> count.txt; exit 7",
        "timeout": "printf sent >> count.txt; sleep 30",
        "missing_command": "./missing-program",
        "missing_shell": "printf sent >> count.txt",
    }
    if failure == "timeout":
        monkeypatch.setattr(tools, "SHELL_TIMEOUT_SECONDS", 1)
    if failure == "missing_shell":
        monkeypatch.setattr(tools, "_shell_command", lambda command: [str(company.root / "missing-shell"), command])
    number = _hold(company, commands[failure])
    conversation = _chat(company)

    for _ in range(2):
        if front_door == "cli":
            assert main(["held", "approve", str(number)]) == 2
            text = capsys.readouterr().out
        else:
            text = conversation.handle(f"/approve {number}").text
        assert "completion is unverified" in text
        assert "already done" not in text and "Ran" not in text

    marker = company.root / "count.txt"
    if failure in ("missing_shell", "missing_command"):
        assert not marker.exists()
    else:
        assert marker.read_text() == "sent"
    orchestrator = company._orchestrator()
    try:
        claim = orchestrator.outside.held_execution(number)
        assert claim is not None and claim.status == "running" and claim.output
        assert orchestrator.outside.held() == []
        assert (
            orchestrator.outside.hold(
                run_id="resumed", role="x/b", command=commands[failure], workspace=company.root, at=NOW
            )
            == number
        )
    finally:
        orchestrator.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_chat_approval_reports_prior_completion_without_repeating_it(company: Company, legacy: bool) -> None:
    number = _hold(company, "printf succeeded >> count.txt")
    assert main(["held", "approve", str(number)]) == 0
    requested = _legacy_duplicate(company, number) if legacy else number
    conversation = _chat(company)
    reply = conversation.handle(f"/approve {requested}").text
    assert f"#{requested} was already done as #{number}" in reply
    assert "Ran" not in reply
    assert company.root.joinpath("count.txt").read_text() == "succeeded"
    assert conversation.handle("/held").text == "Nothing waits."


def test_a_completed_legacy_duplicate_leaves_the_cli_approval_queue_but_keeps_its_history(
    company: Company, capsys: pytest.CaptureFixture[str]
) -> None:
    number = _hold(company, "printf succeeded >> count.txt")
    assert main(["held", "approve", str(number)]) == 0
    duplicate = _legacy_duplicate(company, number)
    capsys.readouterr()
    assert main(["held", "approve", str(duplicate)]) == 0
    assert f"#{duplicate} was already done as #{number}" in capsys.readouterr().out
    assert main(["held"]) == 0
    assert "Nothing is waiting" in capsys.readouterr().out
    orchestrator = company._orchestrator()
    try:
        assert {h.id for h in orchestrator.outside.held(None)} == {number, duplicate}
    finally:
        orchestrator.close()


@pytest.mark.parametrize("completed", [False, True])
def test_chat_approval_rechecks_a_claim_won_by_another_approver(
    company: Company, monkeypatch: pytest.MonkeyPatch, completed: bool
) -> None:
    number = _hold(company, "printf succeeded >> count.txt")
    original = Orchestrator.run_held

    def other_approver(self: Orchestrator, held_id: int, execute: Execute, *, by: str) -> None:
        if completed:
            original(self, held_id, tools.execute, by="another person")
        else:
            assert self.outside.decide_held(held_id, "running", by="another person", at=NOW)
        return None

    monkeypatch.setattr(Orchestrator, "run_held", other_approver)
    reply = _chat(company).handle(f"/approve {number}").text
    assert "Ran" not in reply
    assert ("already done" if completed else "completion is unverified") in reply
    marker = company.root / "count.txt"
    if completed:
        assert marker.read_text() == "succeeded"
    else:
        assert not marker.exists()


@pytest.mark.parametrize("output", ["exit code 2304\nsent", "Stopped after 300 seconds.\nsent"])
def test_reopening_a_prior_failed_approval_preserves_uncertainty(company: Company, output: str) -> None:
    number = _hold(company, "printf sent >> count.txt; kill -KILL $$")
    orchestrator = company._orchestrator()
    try:
        assert orchestrator.outside.decide_held(number, "approved", by="old code", at=NOW, output=output)
    finally:
        orchestrator.close()
    reply = _chat(company).handle(f"/approve {number}").text
    assert "completion is unverified" in reply and "already done" not in reply
    assert not company.root.joinpath("count.txt").exists()


def test_reopening_a_success_with_long_output_keeps_its_completion(company: Company) -> None:
    number = _hold(company, "printf sent >> count.txt; printf '%05000d' 0")
    assert main(["held", "approve", str(number)]) == 0
    reply = _chat(company).handle(f"/approve {number}").text
    assert "already done" in reply and "unverified" not in reply
    assert company.root.joinpath("count.txt").read_text() == "sent"


def test_chat_approval_of_an_interrupted_legacy_duplicate_reports_uncertainty(company: Company) -> None:
    number = _hold(company, "printf sent >> count.txt; kill -KILL $$")
    assert main(["held", "approve", str(number)]) == 2
    duplicate = _legacy_duplicate(company, number)
    conversation = _chat(company)
    reply = conversation.handle(f"/approve {duplicate}").text
    assert "completion is unverified" in reply and "Ran" not in reply and "already done" not in reply
    assert company.root.joinpath("count.txt").read_text() == "sent"
    assert conversation.handle("/held").text == "Nothing waits."
