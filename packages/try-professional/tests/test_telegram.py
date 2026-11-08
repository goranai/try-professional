"""Talking to the company from Telegram, against a fake Bot API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from try_professional import Company
from try_professional.adapters.api_runner.scripted import ScriptedClient
from try_professional.telegram import Bot, allowed_chats

OWNER = 111
STRANGER = 999


class FakeTelegram:
    def __init__(self, messages: list[tuple[int, str]]) -> None:
        self.pending = [
            {"update_id": n, "message": {"chat": {"id": c}, "text": t}} for n, (c, t) in enumerate(messages)
        ]
        self.sent: list[tuple[int, str]] = []

    def __call__(self, method: str, params: dict[str, Any]) -> Any:
        if method == "getUpdates":
            ready = [u for u in self.pending if u["update_id"] >= int(params["offset"])]
            return ready[:1]
        if method == "sendMessage":
            self.sent.append((int(params["chat_id"]), str(params["text"])))
            return {}
        raise AssertionError(method)


@pytest.fixture
def company(tmp_path: Path) -> Company:
    return Company.create(tmp_path / "acme", name="Acme")


def test_only_allowed_chats_give_work(company: Company) -> None:
    fake = FakeTelegram([(STRANGER, "Delete everything"), (OWNER, "Write a two-line launch note")])
    bot = Bot(fake, {OWNER}, say=lambda text: None)
    bot.serve(company.root, once=True, client=ScriptedClient(), model="scripted")
    assert not any(chat == STRANGER for chat, _ in fake.sent)
    bot.serve(company.root, once=True, client=ScriptedClient(), model="scripted")
    replies = [text for chat, text in fake.sent if chat == OWNER]
    assert replies[0] == "Working on it..."
    assert "The work:" in replies[-1]


def test_a_long_reply_is_split(company: Company) -> None:
    fake = FakeTelegram([])
    Bot(fake, {OWNER}).send(OWNER, "x" * 9000)
    assert len(fake.sent) == 3


def test_allowed_chat_ids_are_read_carefully() -> None:
    assert allowed_chats("111, -100222,abc,") == {111, -100222}
    assert allowed_chats("") == set()
