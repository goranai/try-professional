"""Talking to the company: one message at a time, through the CEO, with questions asked in the conversation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from try_professional import Company
from try_professional.adapters.api_runner.scripted import ScriptedClient
from try_professional.chat import Conversation, terminal_chat
from try_professional.packs import DEFAULT_GATES


@pytest.fixture
def company(tmp_path: Path) -> Company:
    return Company.create(tmp_path / "acme", name="Acme")


def _conversation(company: Company, answers: list[tuple[str, str]] | None = None) -> tuple[Conversation, list[str]]:
    asked: list[str] = []
    queue = list(answers or [])

    def ask_person(question: str, options: list[str]) -> tuple[str, str]:
        asked.append(question)
        return queue.pop(0) if queue else ("", "")

    conversation = Conversation(
        company.root, ask_person=ask_person, approve=None, client=ScriptedClient(), model="scripted"
    )
    return conversation, asked


def _gate_without_reviewer(company: Company) -> Company:
    """Marketing work is public, and a legal gate with no Legal department means the run has to ask a person."""
    company = Company.create(company.root.parent / "marketing-only", name="Acme", packs=["marketing"])
    path = company.root / "org" / "org.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["gates"] = yaml.safe_load(yaml.safe_dump(DEFAULT_GATES))
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return company


def test_a_message_comes_back_as_the_ceos_answer(company: Company) -> None:
    conversation, _ = _conversation(company)
    reply = conversation.handle("Write a two-line launch note")
    assert reply.status == "done"
    assert "The work:" in reply.text
    assert company.deliverables(reply.run_id) is not None


def test_it_remembers_the_conversation(company: Company) -> None:
    conversation, _ = _conversation(company)
    conversation.handle("Write a two-line launch note")
    second = conversation.handle("Now a shorter one")
    goal = company.status(second.run_id).goal
    assert "Now a shorter one" in goal
    assert "Earlier in this conversation" in goal and "launch note" in goal
    assert len(conversation.history()) == 2


def test_a_question_is_asked_in_the_conversation(company: Company) -> None:
    company = _gate_without_reviewer(company)
    conversation, asked = _conversation(company, [("waive", "Internal draft, I checked it")] * 3)
    reply = conversation.handle("Write a two-line launch note")
    assert asked and "legal" in asked[0].lower()
    assert reply.status == "done"


def test_an_unanswered_question_can_be_continued(company: Company) -> None:
    company = _gate_without_reviewer(company)
    conversation, _ = _conversation(company)
    stopped = conversation.handle("Write a two-line launch note")
    assert stopped.status == "blocked" and "trypro decide" in stopped.text
    conversation.ask_person = lambda question, options: ("waive", "Fine")
    resumed = conversation.handle("continue")
    assert resumed.run_id == stopped.run_id and resumed.status == "done"


def test_slash_commands(company: Company) -> None:
    conversation, _ = _conversation(company)
    assert "continue" in conversation.handle("/help").text
    assert conversation.handle("/runs").text == "No commands yet."
    run_id = conversation.handle("Write a two-line launch note").run_id
    assert run_id in conversation.handle("/runs").text


def test_the_terminal_chat(company: Company, monkeypatch: pytest.MonkeyPatch) -> None:
    said: list[str] = []
    typed = iter(["Write a two-line launch note", "/quit"])
    original = Conversation.__init__

    def scripted(self: Any, root: Any, **kwargs: Any) -> None:
        original(self, root, **{**kwargs, "client": ScriptedClient(), "model": "scripted"})

    monkeypatch.setattr(Conversation, "__init__", scripted)
    assert terminal_chat(company.root, say=said.append, read=lambda prompt: next(typed)) == 0
    assert any("You're talking to Acme" in line for line in said)
    assert any("The work:" in line for line in said)
