"""A company on a timer, working while nobody is watching.

`trypro schedule run` does one round: it checks what the company put out and queues follow-ups for new replies, runs
every schedule that's due, and runs those follow-ups. With nobody there, a command that changes something outside
isn't run or refused: it's held, and a person approves it later with `trypro held approve`, or from chat. If a
Telegram bot is set up, the round's summary is sent to its chats.

There's no daemon. The operating system's own scheduler runs a round: `trypro schedule install` writes that entry.
`trypro schedule run --watch` is the same thing in a terminal, for as long as it stays open.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .api import Company
from .core.lock import command_lock
from .core.orchestrator import utc_now
from .outcomes import FOLLOW_UP_MARK, Gh, check, run_gh

Say = Callable[[str], None]
WATCH_SECONDS = 300
INSTALL_EVERY_MINUTES = 30


@dataclass
class Round:
    ran: list[tuple[str, str, str]] = field(default_factory=list)  # (what, run_id, status)
    settled: list[str] = field(default_factory=list)
    follow_ups: int = 0
    held: int = 0
    problems: list[str] = field(default_factory=list)
    busy: bool = False

    def summary(self, company: str) -> str:
        if self.busy:
            return f"{company}: another command was running, so this round was skipped."
        lines = [f"{company}: a scheduled round."]
        lines += [f"- {what}: {status} ({run_id})" for what, run_id, status in self.ran]
        lines += [f"- {line}" for line in self.settled]
        if self.follow_ups:
            lines.append(f"- {self.follow_ups} new {'reply needs' if self.follow_ups == 1 else 'replies need'} a look")
        if self.held:
            lines.append(f"- {self.held} waiting for your approval: trypro held, or /held in chat")
        lines += [f"- Couldn't check: {problem}" for problem in self.problems]
        return "\n".join(lines) if len(lines) > 1 else f"{company}: nothing was due."


def run_round(
    root: Path,
    *,
    say: Say = print,
    client: Any = None,
    model: str = "",
    dry_run: bool = False,
    gh: Gh = run_gh,
    now: Callable[[], str] = utc_now,
) -> Round:
    company = Company.open(root)
    runner, orchestrator = company._runner(dry_run, client, model, None)
    runner.hold = True  # Nobody is here to ask, so outside commands wait for a person.
    result = Round()
    try:
        with command_lock(orchestrator.home) as held:
            if not held:
                result.busy = True
                return result
            report = check(orchestrator, gh=gh, now=now)
            result.problems += report.problems
            result.follow_ups = len(report.follow_ups)
            result.settled = [f"{item.status}: {item.url}" for item in report.settled]
            for schedule in orchestrator.outside.due(now()):
                say(f"Running schedule {schedule.name}: {schedule.goal[:80]}")
                run = runner.run(schedule.goal)
                orchestrator.outside.ran(schedule.name, now(), run.run_id)
                result.ran.append((schedule.name, run.run_id, run.status))
            for entry in orchestrator.company.queued():
                if not entry["goal"].startswith(FOLLOW_UP_MARK):
                    continue  # A person queued this one, and runs it with `trypro next`.
                if orchestrator.company.take_queued(int(entry["id"])) is None:
                    continue
                say(f"Following up: {entry['goal'][:100]}")
                run = runner.run(entry["goal"])
                result.ran.append(("follow-up", run.run_id, run.status))
            result.held = len(orchestrator.outside.held())
    finally:
        orchestrator.close()
    return result


def notify(text: str, say: Say = print) -> None:
    """Send a round's summary to the company's Telegram chats, if a bot is set up. Never fails the round."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return
    from .telegram import Bot, TelegramError, allowed_chats, bot_api

    chats = allowed_chats(os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", ""))
    bot = Bot(bot_api(token), chats, say=say)
    for chat in sorted(chats):
        try:
            bot.send(chat, text)
        except TelegramError as error:
            say(f"Couldn't tell Telegram chat {chat}: {error}")


def watch(root: Path, *, say: Say = print, every_seconds: int = WATCH_SECONDS) -> int:
    say(f"Running a round every {every_seconds // 60} minutes. Stop with Ctrl+C.")
    try:
        while True:
            outcome = run_round(root, say=say)
            text = outcome.summary(Company.open(root).org.company_name)
            say(text)
            if outcome.ran or outcome.settled or outcome.held:
                notify(text, say)
            time.sleep(every_seconds)
    except KeyboardInterrupt:
        say("Stopped.")
    return 0


def _task_name(root: Path) -> str:
    return "trypro-" + re.sub(r"[^A-Za-z0-9]+", "-", root.resolve().name).strip("-").lower()


def install_entry(root: Path, minutes: int = INSTALL_EVERY_MINUTES) -> tuple[str, list[str] | None]:
    """The operating system's entry for a round every few minutes: a line to show, and a command that adds it."""
    folder = str(root.resolve())
    run = f'"{sys.executable}" -m try_professional.cli schedule run --dir "{folder}"'
    if sys.platform == "win32":
        command = ["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", str(minutes), "/TN", _task_name(root)]
        command += ["/TR", run]
        return subprocess.list2cmdline(command), command
    log = Path(folder) / ".trypro" / "schedule.log"
    line = f'*/{minutes} * * * * {run} >> "{log}" 2>&1'
    return line, None


def apply_entry(root: Path, minutes: int = INSTALL_EVERY_MINUTES) -> str:
    shown, command = install_entry(root, minutes)
    if command is not None:
        done = subprocess.run(command, capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise RuntimeError((done.stderr or done.stdout).strip() or "schtasks failed")
        name = _task_name(root)
        return f"Added a Windows scheduled task, {name}. Remove it with: schtasks /Delete /TN {name}"
    if shutil.which("crontab") is None:
        raise RuntimeError("There's no crontab here. Add this line to your scheduler yourself:\n" + shown)
    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False).stdout
    if shown in current:
        return "It's already in your crontab."
    updated = (current.rstrip("\n") + "\n" if current.strip() else "") + shown + "\n"
    subprocess.run(["crontab", "-"], input=updated, text=True, check=True)
    return "Added to your crontab. Remove it with: crontab -e"
