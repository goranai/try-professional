"""Talk to your company. You say what you want, the CEO takes it down the hierarchy, and the answer comes back.

One conversation core serves every front door: the terminal (`trypro chat`) and chat apps (`trypro telegram`). Each
message is a command for the company, run by its own engine with whatever model `trypro.yaml` names. A question for a
person, or a command that changes something outside, is asked right there, in the conversation.
"""

from __future__ import annotations

import getpass
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .adapters.api_runner.runner import RunResult
from .api import Company
from .core.pipeline import AskHuman, HumanDecided

HISTORY_FILE = "conversation.jsonl"
REMEMBERED_EXCHANGES = 3
MAX_REMEMBERED_CHARS = 300

# Asked of the person: (a question, its options) -> their choice, and a note. Return "" to leave it unanswered.
AskPerson = Callable[[str, list[str]], tuple[str, str]]
Approve = Callable[[str, str], bool]

HELP = """Say what you want, in your own words, and the company works on it.
  continue       Carry on with the last command that stopped
  /runs          Your recent commands, and how they ended
  /held          Commands waiting for your approval, such as posting an issue
  /approve 3     Run held command 3        /deny 3   Refuse it
  /outcomes      What the company put out, and what came of it
  /outcome 3 rejected "too expensive"   Tell it what happened. Everyone who worked on it learns it
  /schedules     What it does on a timer
  /help          This
  /quit          Leave. The company remembers this conversation."""


@dataclass
class Reply:
    text: str
    run_id: str = ""
    status: str = ""


class Conversation:
    """A conversation with one company. Not thread-safe: one message at a time, as a person types them."""

    def __init__(
        self,
        root: str | Path,
        *,
        ask_person: AskPerson,
        approve: Approve | None,
        client: Any = None,
        model: str = "",
        person: str = "",
    ) -> None:
        self.company = Company.open(root)
        self.ask_person = ask_person
        self.approve = approve
        self.client = client
        self.model = model
        self.person = person or getpass.getuser()
        self._lock = threading.Lock()

    @property
    def history_path(self) -> Path:
        return self.company.root / ".trypro" / HISTORY_FILE

    def history(self) -> list[dict[str, str]]:
        try:
            lines = self.history_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [json.loads(line) for line in lines if line.strip()]

    def handle(self, message: str) -> Reply:
        message = message.strip()
        if not message:
            return Reply("Say what you want the company to do.")
        if message.startswith("/") and message.lower() != "/continue":
            return self._command(message)
        stopped = self._last_stopped()
        if message.lower().strip("/ .!") == "continue" and stopped:
            result = self._run(stopped, resume=True)
        else:
            result = self._run(self._with_context(message))
        reply = self._reply(result)
        self._remember(message, reply)
        return reply

    # ---- the company's work -------------------------------------------------------------------------------------

    def _run(self, goal_or_run: str, *, resume: bool = False) -> RunResult:
        runner, orchestrator = self.company._runner(False, self.client, self.model, self._approve)
        runner.on_human = self._on_human
        try:
            return runner.resume(goal_or_run) if resume else runner.run(goal_or_run)
        finally:
            orchestrator.close()

    def _last_stopped(self) -> str:
        """The last command that stopped before it was done, if it's still waiting."""
        for entry in reversed(self.history()):
            if entry.get("run_id") and entry.get("status") == "blocked":
                return str(entry["run_id"]) if self.company.status(str(entry["run_id"])).phase == "blocked" else ""
        return ""

    def _approve(self, role: str, command: str) -> bool:
        if self.approve is None:
            return False
        with self._lock:  # Roles can work at once, and a person answers one question at a time.
            return self.approve(role, command)

    def _on_human(self, question: AskHuman) -> HumanDecided | None:
        with self._lock:
            choice, note = self.ask_person(question.question, list(question.options))
        if choice not in question.options:
            return None
        return HumanDecided(request_id=question.request_id, decision=choice, note=note, decided_by=self.person)

    def _with_context(self, message: str) -> str:
        """The command, with the last few exchanges, so "do the same for the next one" means something."""
        earlier = self.history()[-REMEMBERED_EXCHANGES:]
        if not earlier:
            return message
        lines = [f"- They asked: {e['message'][:MAX_REMEMBERED_CHARS]} -> {e['outcome'][:MAX_REMEMBERED_CHARS]}"
                 for e in earlier]  # fmt: skip
        return f"{message}\n\nEarlier in this conversation, for context:\n" + "\n".join(lines)

    def _reply(self, result: RunResult) -> Reply:
        where = self.company.deliverables(result.run_id)
        if result.status == "done":
            report = self.company.final_report(result.run_id) or {}
            summary = str(report.get("summary_in_user_language") or "Done.")
            limits = [str(item) for item in report.get("known_limits") or []]
            text = summary + ("\n\nWhat to check: " + "; ".join(limits) if limits else "")
            text += f"\n\nThe work: {where}" if where else ""
            return Reply(text, result.run_id, "done")
        status = self.company.status(result.run_id)
        if status.questions:
            text = "It stopped to ask you:\n" + "\n".join(f"- {q}" for q in status.questions)
            text += f"\n\nAnswer with: trypro decide {result.run_id} <request-id> <choice>, then say: continue"
        else:
            text = f"It stopped: {result.reason or status.phase}."
        if where:
            text += f"\n\nWhat it made so far, not approved: {where}"
        return Reply(text, result.run_id, result.status)

    def _remember(self, message: str, reply: Reply) -> None:
        entry = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "message": message,
            "run_id": reply.run_id,
            "status": reply.status,
            "outcome": reply.text.split("\n", 1)[0],
        }
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ---- slash commands -------------------------------------------------------------------------------------------

    def _command(self, message: str) -> Reply:
        name = message.split()[0].lower()
        if name in ("/help", "/?"):
            return Reply(HELP)
        if name == "/runs":
            rows = []
            for run_id in self.company.runs()[:10]:
                status = self.company.status(run_id)
                rows.append(f"{run_id}  {status.phase:<9} {status.goal.splitlines()[0][:70]}")
            return Reply("\n".join(rows) or "No commands yet.")
        if name in ("/held", "/approve", "/deny", "/outcomes", "/outcome", "/schedules"):
            return self._outside(name, message.split()[1:])
        return Reply(f"Unknown command {name}. /help lists them.")

    def _settle(self, args: list[str]) -> Reply:
        """What happened to something the company sent, as a person tells it: a client's answer, a post's reception.
        Every role that worked on it learns it, the same way a checked GitHub verdict is learned."""
        from .cli import _outside_orchestrator
        from .core.outside import OUTCOME_STATUSES

        if len(args) < 2 or not args[0].lstrip("#").isdigit() or args[1].lower() not in OUTCOME_STATUSES:
            return Reply(
                f"Say: /outcome <number> <{'|'.join(OUTCOME_STATUSES)}> <what was said>. /outcomes lists them."
            )
        number, status, said = int(args[0].lstrip("#")), args[1].lower(), " ".join(args[2:])
        orchestrator = _outside_orchestrator(self.company.root, with_memory=True)
        try:
            if orchestrator.outside.outcome(number) is None:
                return Reply(f"There's no #{number}. /outcomes lists them.")
            changed = orchestrator.settle_outcome(number, status, said, by=self.person)
        finally:
            orchestrator.close()
        if changed is None:
            return Reply(f"#{number} was already {status}.")
        return Reply(f"#{number} is {status}. Everyone who worked on it learns that, with what you said.")

    def _outside(self, name: str, args: list[str]) -> Reply:
        """Approvals, outcomes, and schedules. Someone in this conversation is the person the company works for."""
        from .adapters.api_runner.tools import execute
        from .core.outside import describe_every

        orchestrator = self.company._orchestrator()
        try:
            outside = orchestrator.outside
            if name == "/held":
                rows = [
                    f"#{h.id} {h.role}: {h.command}" + (f"\n{h.checks}" if h.checks else "") for h in outside.held()
                ]
                return Reply(
                    "\n".join([*rows, "", "/approve <number> or /deny <number>"]) if rows else "Nothing waits."
                )
            if name == "/outcomes":
                items = outside.outcomes(limit=15)
                if not items:
                    return Reply("Nothing tracked yet.")
                rows = [f"#{i.id} {i.status}: {i.kind} to {i.target or '?'}, {i.ref} {i.title}".strip() for i in items]
                return Reply("\n".join([*rows, "", "/outcome <number> <verdict> <what was said>"]))
            if name == "/outcome":
                return self._settle(args)
            if name == "/schedules":
                rows = [f"{s.name}: every {describe_every(s.every_seconds)}, next {s.next_at[:16]}. {s.goal[:80]}"
                        for s in outside.schedules()]  # fmt: skip
                return Reply("\n".join(rows) or 'None. Add one: trypro schedule add <name> "<what>" --every 1d')
            if not args or not args[0].lstrip("#").isdigit():
                return Reply(f"Which one? {name} <number>. /held lists them.")
            number = int(args[0].lstrip("#"))
            held = outside.held_item(number)
            if held is None or held.status != "waiting":
                return Reply(f"#{number} isn't waiting for approval.")
            if name == "/deny":
                orchestrator.deny_held(number, by=self.person)
                return Reply(f"Refused #{number}. It won't run.")
            done = orchestrator.run_held(number, execute, by=self.person)
            return Reply(f"Ran #{number}.\n{done.output[-1500:] if done else ''}".strip())
        finally:
            orchestrator.close()


def terminal_chat(root: Path, *, say: Callable[[str], None] = print, read: Callable[[str], str] = input) -> int:
    """`trypro chat`: a conversation with the company, in this terminal."""

    def ask_person(question: str, options: list[str]) -> tuple[str, str]:
        say(f"\nThe company asks: {question}")
        choice = read(f"Choose one of {', '.join(options)}: ").strip()
        note = read("A note, if any: ").strip() if choice else ""
        return choice, note

    def approve(role: str, command: str) -> bool:
        say(f"\n{role} wants to run a command that changes something outside:\n  {command}")
        return read("Allow it? [y/N]: ").strip().lower().startswith("y")

    conversation = Conversation(root, ask_person=ask_person, approve=approve)
    say(f"You're talking to {conversation.company.org.company_name}. Say what you want; /help for more.")
    while True:
        try:
            message = read("\nYou: ")
        except (EOFError, KeyboardInterrupt):
            say("")
            return 0
        if message.strip().lower() in ("/quit", "/exit", "quit", "exit"):
            return 0
        if not message.strip():
            continue
        say("Working on it...")
        say(f"\n{conversation.handle(message).text}")
