"""`trypro telegram`: talk to your company from Telegram.

Set TELEGRAM_BOT_TOKEN (from @BotFather) and TELEGRAM_ALLOWED_CHAT_IDS (your chat's id, or several, comma-separated)
in .env. Only those chats can give the company work. Anyone else gets no answer. It uses Telegram's Bot API directly,
by long polling, with no dependency. A question for a person, or a command that changes something outside, is sent to
the chat that gave the work, and the next reply there answers it.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .chat import Conversation

API = "https://api.telegram.org/bot{token}/{method}"
POLL_SECONDS = 50
MAX_MESSAGE = 4000
ANSWER_WAIT_SECONDS = 3600

Call = Callable[[str, dict[str, Any]], Any]


class TelegramError(Exception):
    pass


def bot_api(token: str) -> Call:
    """Calls the Bot API. The token is in the URL, so errors never include the URL."""

    def call(method: str, params: dict[str, Any]) -> Any:
        data = urllib.parse.urlencode({k: str(v) for k, v in params.items()}).encode()
        request = urllib.request.Request(API.format(token=token, method=method), data=data)
        try:
            with urllib.request.urlopen(request, timeout=POLL_SECONDS + 15) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError) as error:
            raise TelegramError(f"Telegram didn't answer {method}: {type(error).__name__}") from None
        if not body.get("ok"):
            raise TelegramError(f"Telegram refused {method}: {body.get('description', 'no reason given')}")
        return body.get("result")

    return call


class Bot:
    def __init__(self, call: Call, allowed: set[int], *, say: Callable[[str], None] = print) -> None:
        self.call = call
        self.allowed = allowed
        self.say = say
        self.offset = 0

    def send(self, chat: int, text: str) -> None:
        for start in range(0, max(len(text), 1), MAX_MESSAGE):
            self.call("sendMessage", {"chat_id": chat, "text": text[start : start + MAX_MESSAGE] or "..."})

    def updates(self, timeout: int = POLL_SECONDS) -> list[tuple[int, str]]:
        """New text messages from allowed chats. Others are dropped, unanswered."""
        found = []
        for update in self.call("getUpdates", {"offset": self.offset, "timeout": timeout}) or []:
            self.offset = max(self.offset, int(update["update_id"]) + 1)
            message = update.get("message") or {}
            chat = int((message.get("chat") or {}).get("id", 0))
            text = message.get("text")
            if chat in self.allowed and isinstance(text, str):
                found.append((chat, text))
            elif chat:
                self.say(f"Ignored a message from chat {chat}, which isn't in TELEGRAM_ALLOWED_CHAT_IDS.")
        return found

    def wait_for_reply(self, chat: int) -> str:
        deadline = time.monotonic() + ANSWER_WAIT_SECONDS
        while time.monotonic() < deadline:
            for from_chat, text in self.updates():
                if from_chat == chat:
                    return text
        return ""

    def conversation(self, root: Path, chat: int, client: Any = None, model: str = "") -> Conversation:
        def ask_person(question: str, options: list[str]) -> tuple[str, str]:
            self.send(chat, f"The company asks: {question}\n\nReply with one of: {', '.join(options)}")
            reply = self.wait_for_reply(chat).strip()
            choice, _, note = reply.partition(" ")
            return choice.lower(), note.strip()

        def approve(role: str, command: str) -> bool:
            self.send(
                chat, f"{role} wants to run a command that changes something outside:\n{command}\n\nAllow it? yes / no"
            )
            return self.wait_for_reply(chat).strip().lower().startswith("y")

        return Conversation(
            root, ask_person=ask_person, approve=approve, client=client, model=model, person=f"telegram:{chat}"
        )

    def serve(self, root: Path, *, once: bool = False, client: Any = None, model: str = "") -> None:
        """Answer messages one at a time, until stopped."""
        conversations: dict[int, Conversation] = {}
        while True:
            for chat, text in self.updates():
                conversation = conversations.setdefault(chat, self.conversation(root, chat, client, model))
                if not text.startswith("/"):
                    self.send(chat, "Working on it...")
                try:
                    reply = conversation.handle(text)
                    self.send(chat, reply.text)
                except Exception as error:  # A failed command mustn't stop the bot for everyone.
                    self.send(chat, f"That didn't work: {type(error).__name__}: {str(error)[:300]}")
            if once:
                return


def allowed_chats(value: str) -> set[int]:
    return {int(part) for part in value.replace(" ", "").split(",") if part.lstrip("-").isdigit()}


def serve_from_env(root: Path, say: Callable[[str], None]) -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    allowed = allowed_chats(os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", ""))
    if not token or not allowed:
        say("Set TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_CHAT_IDS in .env first. Only those chats can give it work.")
        say("To find your chat id: message your bot, then open https://api.telegram.org/bot<token>/getUpdates")
        return 2
    bot = Bot(bot_api(token), allowed, say=say)
    me = bot.call("getMe", {})
    say(f"@{me.get('username')} is listening for {len(allowed)} chat(s). Stop it with Ctrl+C.")
    try:
        bot.serve(root)
    except KeyboardInterrupt:
        say("Stopped.")
    return 0
