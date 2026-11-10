"""What the MCP server does, separate from the protocol, so it can be tested directly.

A client drives the pipeline: ask for the next action, do it, submit the artifact. The pipeline
still enforces order, schemas, and gates. Two identity modes:

    bound:    the server was started for one role. Submissions are recorded as attested by the runtime.
    operator: one client plays whichever role an open action names, and it's recorded as "claimed".
              Order, schemas, and gates still hold, but separation between roles is only procedural.

A role is never a tool argument. It comes from how the server started, or from the open
invocation. Nothing here lets a client approve a skill, admit one, or change company memory. A
person's answer, and a change to who works here, come through the person_ tools, which Claude
Code asks the person to confirm on every call, and which a role's own server refuses.

Three identity modes, really: a coordinator is a client that starts work and hands it to bound roles. It can't read an
assignment or submit anything, so it can't quietly do a role's work, however it's asked to or tries to.

A client's own token count can't be trusted for a limit, so the server estimates what it sent and
received, and budgets run on that. What a client reports is kept beside it, marked as self-reported.
"""

from __future__ import annotations

import getpass
import json
import re
from pathlib import Path
from typing import Any

from ...core.artifacts import PAYLOAD_TYPES, Flags, settle_flags
from ...core.audit import read_records
from ...core.orchestrator import Orchestrator, utc_now
from ...core.pipeline import (
    AskHuman,
    EscalationRaised,
    Finish,
    HandoffRaised,
    HumanDecided,
    IllegalEvent,
    InvokeRole,
    Purpose,
    Submission,
    UsageRecorded,
)
from ...core.pipeline.state import ESTIMATED_BY_SERVER
from ...org import Org
from ..api_runner.prompts import system_prompt, untrusted, user_prompt
from ..api_runner.runner import TOOLS_BY_PURPOSE
from ..api_runner.tools import ToolContext, ToolRefused, run_tool, submit_spec
from ..base import Capabilities, Delivery, Mode, ProvisionReport, VerifyReport

MCP_TOOLS = (
    "org_get",
    "run_start",
    "run_status",
    "run_next_action",
    "artifact_submit",
    "review_submit",
    "handoff_request",
    "escalate",
    "memory_recall",
    "memory_write",
    "skill_search",
    "skill_get",
    "workspace_list",
    "workspace_read",
    "workspace_write",
    "usage_report",
    "outcome_track",
    "audit_tail",
)
# What records a person's decisions, on the coordinator's server only. Claude Code asks the person to confirm each.
PERSON_TOOLS = (
    "person_answer",
    "person_decide_company_change",
    "person_decide_memory",
    "person_add_rule",
    "person_remove_rule",
    "outcome_record",
    "company_rules",
)
# All a coordinator may do. Anything else is a role's work.
COORDINATOR_TOOLS = ("org_get", "run_start", "run_status", "run_next_action", "audit_tail", *PERSON_TOOLS)
DEFAULT_AUDIT_TAIL = 20
MAX_AUDIT_TAIL = 200
CHARS_PER_TOKEN = 3.5
# What passes through this server undercounts what a client spends: its own instructions, thinking, and tool calls.
# Over 17 commands measured through Claude Code, the client's fresh tokens (input, cache writes, and output, leaving out
# re-reads of cached text) were 3.3 times this server's count, from 2.3 to 4.6. docs/research/mcp_estimate has the runs.
MCP_ESTIMATE_FACTOR = 3.3

_CONTINUE = re.compile(r"\s*/?(?:company\s+)?continue\s+(run-[0-9a-f]+)\s*", re.IGNORECASE)


class ToolError(Exception):
    """A refusal the client should see and can act on."""


class McpService:
    def __init__(
        self,
        orchestrator: Orchestrator,
        *,
        bound_role: str | None = None,
        coordinator: bool = False,
        computer: bool = True,
        root: Path | None = None,
    ) -> None:
        # The company's folder, where org/ is. Its records are usually in .trypro beside it.
        self.root = root or orchestrator.home.parent
        if bound_role is not None and not orchestrator.org.has_role(bound_role):
            raise ValueError(f"'{bound_role}' isn't a role in this company")
        if bound_role is not None and coordinator:
            raise ValueError("A server is bound to a role, or coordinates. It can't be both.")
        self.coordinator = coordinator
        self.orchestrator = orchestrator
        self.org: Org = orchestrator.org
        self.bound_role = bound_role
        # A bound role is a full agent unless its company was set up sandboxed: its prompt must say which.
        self.computer = computer

    @property
    def attestation(self) -> Any:
        return "runtime" if self.bound_role else "claimed"

    # ---- identity ------------------------------------------------------------------------

    def _role_for(self, run_id: str, invocation_id: str) -> str:
        """The acting role: fixed at launch when bound, and otherwise the role this work was assigned to."""
        invocation = self._invocation(run_id, invocation_id)
        if self.coordinator:
            self._violation(run_id, "COORDINATOR_ACTED", f"The coordinator tried to act as {invocation.role}")
            raise ToolError(f"A coordinator can't do this. Start the subagent for {invocation.role}, and let it.")
        if self.bound_role and invocation.role != self.bound_role:
            self._violation(run_id, "WRONG_ACTOR", f"{self.bound_role} can't act on work assigned to {invocation.role}")
            raise ToolError(f"This server acts as {self.bound_role}. {invocation_id} is assigned to {invocation.role}.")
        return str(invocation.role)

    def _invocation(self, run_id: str, invocation_id: str) -> Any:
        invocation = self.orchestrator.state(run_id).invocations.get(invocation_id)
        if invocation is None:
            raise ToolError(f"'{invocation_id}' isn't open. Call run_next_action.")
        return invocation

    def _violation(self, run_id: str, code: str, message: str) -> None:
        self.orchestrator.record(run_id, "violation", {"code": code, "message": message})

    def _require_tool(self, run_id: str, role: str, tool: str) -> None:
        if tool not in self.org.role(role).allowed_tools:
            self._violation(run_id, "TOOL_REFUSED", f"{role} used {tool}")
            raise ToolError(f"{role} isn't allowed to use {tool}.")

    # ---- reading ---------------------------------------------------------------------------

    def org_get(self) -> dict[str, Any]:
        return {
            "company": self.org.company_name,
            "root_role": self.org.root_role,
            "identity": self.bound_role or ("coordinator" if self.coordinator else "operator"),
            "departments": [
                {"id": d.id, "head": d.head_ref, "activation": d.activation, "roles": [f"{d.id}/{r}" for r in d.roles]}
                for d in self.org.departments.values()
            ],
        }

    def _self_reported(self, run_id: str) -> int:
        records = read_records(self.orchestrator.audit_path(run_id))
        return sum(int(r["data"].get("tokens", 0)) for r in records if r["kind"] == "usage_self_reported")

    def run_status(self, run_id: str) -> dict[str, Any]:
        state = self.orchestrator.state(run_id)
        if self.bound_role:
            # A bound role sees its own work. The whole picture would make the hierarchy fake.
            own = [t for t in state.tasks.values() if t.owner_role == self.bound_role]
            return {
                "run_id": run_id,
                "phase": state.phase.value,
                "goal": state.goal,
                "tasks": [{"id": t.task_id, "status": t.status.value, "owner": t.owner_role} for t in own],
            }
        final = state.artifacts.get(state.final_artifact_id or "")
        return {
            "run_id": run_id,
            "phase": state.phase.value,
            "goal": state.goal,
            "activated_departments": list(state.activated),
            "tasks": [{"id": t.task_id, "status": t.status.value, "owner": t.owner_role} for t in state.tasks.values()],
            "tokens_estimated_by_server": state.usage.total,
            "tokens_self_reported": self._self_reported(run_id),
            "counters": state.counters.model_dump(),
            "final": final.payload if final and state.phase.value == "done" else None,
        }

    def audit_tail(self, run_id: str, count: int = DEFAULT_AUDIT_TAIL) -> list[dict[str, Any]]:
        if self.bound_role:
            raise ToolError("Only the operator reads the audit log. A role sees its own work.")
        limit = max(1, min(count, MAX_AUDIT_TAIL))
        records = read_records(self.orchestrator.audit_path(run_id))[-limit:]
        return [{k: record[k] for k in ("seq", "ts", "kind", "data")} for record in records]

    # ---- the loop ---------------------------------------------------------------------------

    def run_start(self, goal: str) -> dict[str, Any]:
        if self.bound_role and self.bound_role != self.org.root_role:
            raise ToolError(f"Only {self.org.root_role} starts a run. This server acts as {self.bound_role}.")
        # On Haiku, "/company continue <run-id>" was once passed straight to run_start, which made a new run.
        wanted = _CONTINUE.fullmatch(goal)
        if wanted:
            run_id = wanted.group(1)
            if run_id not in {row[0] for row in self.orchestrator.ledger.list_runs()}:
                raise ToolError(f"There's no run {run_id} to continue. Check the run_id.")
            return {"run_id": run_id, "continued": True, **self.run_next_action(run_id)}
        run_id, _ = self.orchestrator.start(goal)
        return {"run_id": run_id, **self.run_next_action(run_id)}

    def run_next_action(self, run_id: str) -> dict[str, Any]:
        actions = self.orchestrator.open_actions(run_id)
        finish = next((a for a in actions if isinstance(a, Finish)), None)
        if finish is not None:
            finished: dict[str, Any] = {
                "status": finish.status,
                "reason": finish.reason,
                "actions": [],
                "final": None if self.bound_role else self.run_status(run_id)["final"],
                "deliverables": None if self.bound_role else self._index_of(run_id, finish.status),
            }
            if self.coordinator:
                finished.update(self._for_the_person(run_id))
            return finished
        questions = [a for a in actions if isinstance(a, AskHuman)]
        if questions:
            return {
                "status": "blocked",
                "actions": [],
                "needs_a_person": [
                    {
                        "question": q.question,
                        "options": q.options,
                        "command": f"trypro decide {run_id} {q.request_id} <option>",
                        "tell_the_person": self._how_to_answer(run_id, q),
                    }
                    for q in questions
                ],
            }
        work = [a for a in actions if isinstance(a, InvokeRole)]
        if self.bound_role:
            work = [a for a in work if a.role == self.bound_role]
        answer: dict[str, Any] = {"status": "working", "actions": [self._describe(run_id, action) for action in work]}
        roles = sorted({action.role for action in work})
        if self.coordinator and len(roles) > 1:
            # Said here, when it matters, because a small model followed the same rule in /company only partly.
            answer["start_together"] = (
                f"{len(roles)} roles are ready: {', '.join(roles)}. Start all their subagents now, in one message, so "
                "they work at the same time. One subagent per role."
            )
        return answer

    def _for_the_person(self, run_id: str) -> dict[str, Any]:
        """What this command left for the person to decide: a role or department to add, and what to remember."""
        company = self.orchestrator.company
        found: dict[str, Any] = {}
        proposed = company.org_proposals("pending", source_run=run_id)
        if proposed:
            found["company_changes"] = [p.line() for p in proposed] + [
                "The CEO proposes these from what this command showed. Ask the person about each, and record what "
                "they say with person_decide_company_change."
            ]
        remember = [p for p in company.pending_memory("company") if p.source_run == run_id]
        if remember:
            found["company_memory"] = [f"#{p.id} {p.text}" for p in remember] + [
                "The CEO proposes the whole company remember these. Ask the person which to keep, and record each "
                "with person_decide_memory."
            ]
        return found

    def _how_to_answer(self, run_id: str, question: AskHuman) -> str:
        """Ready to run, so nobody has to work out a command. Only a person at a terminal can answer."""
        if self.coordinator:
            options = ", ".join(question.options)
            text = (
                f"{question.question}\n\nYour options: {options}. Tell me which, here, and I'll record it with "
                f"person_answer (request {question.request_id}). Claude Code will ask you to confirm. To come back to "
                f"it later in a new chat: /company continue {run_id}"
            )
        else:
            answers = "\n".join(f"  trypro decide {run_id} {question.request_id} {o}" for o in question.options)
            carry_on = f"Call run_next_action with run_id {run_id}"
            text = (
                f"{question.question}\n\nIn a terminal, run one of these:\n{answers}\n\nThen, to carry on:\n"
                f"  {carry_on}"
            )
        drafts = self.orchestrator.drafts_path(run_id)
        if drafts is not None and (drafts / "index.md").is_file():
            text += f"\n\nWhat it made so far, not approved: {(drafts / 'index.md').as_posix()}"
        return text

    def _index_of(self, run_id: str, status: str) -> str | None:
        """Where a person finds the result, so whoever is coordinating can tell them."""
        folder = self.orchestrator.deliverables_path(run_id)
        return str(folder / "index.md").replace("\\", "/") if folder and status == "done" else None

    def _charge(self, run_id: str, invocation_id: str, role: str, sent: str = "", received: str = "") -> None:
        """Budgets can't rest on what a client says it spent, so this counts what passed through here."""
        self.orchestrator.handle(
            run_id,
            UsageRecorded(
                invocation_id=invocation_id,
                role=role,
                model=ESTIMATED_BY_SERVER,
                input_tokens=round(len(sent) / CHARS_PER_TOKEN * MCP_ESTIMATE_FACTOR),
                output_tokens=round(len(received) / CHARS_PER_TOKEN * MCP_ESTIMATE_FACTOR),
            ),
        )

    def _assignment(self, state: Any, invocation: Any) -> str:
        role = self.org.role(invocation.role)
        briefing = self.orchestrator.briefing(state.run_id, invocation)
        workspace = self._workspace_files(state.run_id)
        loadout = self.orchestrator.skills.loadout(role.ref) if invocation.purpose == Purpose.EXECUTE else []
        computer = self.computer and bool(self.bound_role)
        return user_prompt(invocation, state, self.org, workspace, loadout, briefing=briefing, computer=computer)

    def _workspace_files(self, run_id: str) -> list[str] | None:
        """Files only a bound role is limited to. An operator's client has its own tools, so this can't speak for it."""
        if not self.bound_role:
            return None
        root = self.orchestrator.workspace(run_id).resolve()
        return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file())

    def _describe(self, run_id: str, action: InvokeRole) -> dict[str, Any]:
        if self.coordinator:
            # Who's next, and nothing of what it was asked. It reaches the role through that role's own server.
            return {
                "invocation_id": action.invocation_id,
                "role": action.role,
                "purpose": action.purpose.value,
                "task_id": action.task_id,
            }
        state = self.orchestrator.state(run_id)
        invocation = state.invocations[action.invocation_id]
        role = self.org.role(action.role)
        declares_flags = action.purpose == Purpose.EXECUTE
        usable = [t for t in role.allowed_tools if t in TOOLS_BY_PURPOSE[action.purpose] and t in MCP_TOOLS]
        return {
            "invocation_id": action.invocation_id,
            "role": action.role,
            "purpose": action.purpose.value,
            "output_type": action.output_type,
            "task_id": action.task_id,
            "gate": action.gate,
            "instructions": system_prompt(role, self.org),
            "assignment": self._assignment(state, invocation),
            "output_schema": submit_spec(action.output_type, declares_flags=declares_flags).input_schema,
            "may_use": usable,
            "submit_with": "review_submit" if action.purpose == Purpose.REVIEW else "artifact_submit",
        }

    def _send(self, run_id: str, event: Any, invocation_id: str) -> dict[str, Any]:
        try:
            self.orchestrator.handle(run_id, event)
        except IllegalEvent as error:
            return {"accepted": False, "code": error.code, "reason": str(error)}
        reopened = self.orchestrator.state(run_id).invocations.get(invocation_id)
        if isinstance(event, Submission) and reopened is not None and reopened.feedback:
            # Validation failed, and the pipeline kept the work open with the reason.
            return {"accepted": False, "code": "REJECTED", "reason": reopened.feedback[-1]}
        return {"accepted": True, **self.run_next_action(run_id)}

    def artifact_submit(
        self,
        run_id: str,
        invocation_id: str,
        payload: dict[str, Any],
        flags: dict[str, bool] | None = None,
        language: str | None = None,
        notes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        role = self._role_for(run_id, invocation_id)
        state = self.orchestrator.state(run_id)
        invocation = state.invocations[invocation_id]
        sent = system_prompt(self.org.role(role), self.org) + self._assignment(state, invocation)
        self._charge(run_id, invocation_id, role, sent, json.dumps(payload, ensure_ascii=False))
        if "flags" not in PAYLOAD_TYPES[invocation.output_type].model_fields:
            payload, flags = settle_flags(payload, flags)
        try:
            parsed_flags = Flags.model_validate(flags or {})
        except ValueError as error:
            return {"accepted": False, "code": "REJECTED", "reason": str(error)}
        if not self._invocation_open(run_id, invocation_id):
            return self.run_next_action(run_id)
        event = Submission(
            invocation_id=invocation_id,
            actor=role,
            attested_by=self.attestation,
            payload=payload,
            flags=parsed_flags,
            language=language,
            created_at=self.orchestrator.clock(),
            notes=notes,
        )
        return self._send(run_id, event, invocation_id)

    def review_submit(self, run_id: str, invocation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._invocation(run_id, invocation_id).purpose != Purpose.REVIEW:
            raise ToolError(f"{invocation_id} isn't a review. Use artifact_submit.")
        return self.artifact_submit(run_id, invocation_id, payload)

    def handoff_request(self, run_id: str, invocation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        role = self._role_for(run_id, invocation_id)
        event = HandoffRaised(invocation_id=invocation_id, actor=role, attested_by=self.attestation, payload=payload)
        return self._send(run_id, event, invocation_id)

    def escalate(self, run_id: str, invocation_id: str, reason: str, recommendation: str = "") -> dict[str, Any]:
        role = self._role_for(run_id, invocation_id)
        event = EscalationRaised(
            invocation_id=invocation_id,
            actor=role,
            attested_by=self.attestation,
            payload={"reason": reason, "recommendation": recommendation},
        )
        return self._send(run_id, event, invocation_id)

    def _invocation_open(self, run_id: str, invocation_id: str) -> bool:
        """Charging for work can use up a budget, which blocks the run before this submission arrives."""
        state = self.orchestrator.state(run_id)
        return invocation_id in state.invocations and state.phase.value != "blocked"

    def outcome_track(
        self, run_id: str, invocation_id: str, ref: str, title: str = "", target: str = "", kind: str = ""
    ) -> dict[str, Any]:
        """A role put something out. Any role may ask; code records it, and a check or a person says what came of it."""
        role = self._role_for(run_id, invocation_id)
        item = self.orchestrator.track_outcome(run_id, role, ref, title=title, target=target, kind=kind)
        if item is None:
            raise ToolError("Give a link, or a reference a person would know it by, such as 'quote 12 to Acme'.")
        return {"tracked": True, "id": item.id, "kind": item.kind, "target": item.target}

    # ---- what only a person decides, answered in chat ----------------------------------------------
    # Claude Code asks the person to confirm each of these calls, in every permission mode, because
    # write_claude_code_files gives them an "ask" rule. That prompt is the person's decision, where a terminal was.

    def _for_a_person(self, what: str) -> str:
        if not self.coordinator:
            raise ToolError(
                f"Only the person decides this, through Claude Code's coordinator. This server can't {what}."
            )
        return f"{getpass.getuser()}, confirmed in chat"

    def person_answer(self, run_id: str, request_id: str, option: str, note: str = "") -> dict[str, Any]:
        """Record the person's answer to a run's question, exactly as they gave it."""
        by = self._for_a_person("answer a run's question")
        try:
            self.orchestrator.handle(
                run_id, HumanDecided(request_id=request_id, decision=option, note=note, decided_by=by)
            )
        except IllegalEvent as error:
            raise ToolError(str(error)) from error
        return {"recorded": True, "decided_by": by, "next": f"Call run_next_action with run_id {run_id}"}

    def person_decide_company_change(self, proposal_id: int, decision: str) -> dict[str, Any]:
        """Add or reject a role or department the CEO proposed, as the person said."""
        from ...api import Company, CompanyError

        by = self._for_a_person("change who works here")
        if decision not in ("add", "reject"):
            raise ToolError("decision is add or reject.")
        company = Company(self.root)
        try:
            added = company.decide_org_change(proposal_id, decision == "add", by=by)
        except CompanyError as error:
            raise ToolError(str(error)) from error
        if added is None:
            return {"rejected": proposal_id, "note": "It won't be proposed again."}
        return {
            "added": added.ref,
            "title": added.title,
            "tell_the_person": "Restart Claude Code in this folder, so it loads the new role. It works from the next "
            "command.",
        }

    def person_add_rule(self, text: str) -> dict[str, Any]:
        """A standing rule from the person: every role sees it on every step, from the next step on."""
        by = self._for_a_person("add a company rule")
        try:
            number = self.orchestrator.company.add_rule(text, by=by, at=utc_now())
        except ValueError as error:
            raise ToolError(str(error)) from error
        return {"added": number, "rules": self.company_rules()["rules"]}

    def person_remove_rule(self, number: int) -> dict[str, Any]:
        self._for_a_person("remove a company rule")
        removed = self.orchestrator.company.remove_rule(number, at=utc_now())
        if removed is None:
            raise ToolError(f"There's no rule #{number}. company_rules lists them.")
        return {"removed": removed, "rules": self.company_rules()["rules"]}

    def person_decide_memory(self, number: int, decision: str) -> dict[str, Any]:
        """Keep or drop something the CEO proposed the whole company should remember, as the person said."""
        by = self._for_a_person("change company memory")
        if decision not in ("keep", "drop"):
            raise ToolError("decision is keep or drop.")
        pending = {p.id: p for p in self.orchestrator.company.pending_memory("company")}
        if number not in pending:
            raise ToolError(f"#{number} isn't waiting for a decision. company_rules lists what is.")
        if decision == "keep":
            # Saved first: a lesson that couldn't be saved stays waiting, rather than marked kept and lost.
            self.orchestrator.memory.promote(
                self.org.root_role, pending[number].text, f"{pending[number].source}, kept by {by}"
            )
        self.orchestrator.company.decide_memory(number, decision == "keep", by=by, at=utc_now())
        return {"kept" if decision == "keep" else "dropped": number}

    def company_rules(self) -> dict[str, Any]:
        """The standing rules, and what the CEO proposed the company remember, waiting for the person."""
        if not self.coordinator:
            raise ToolError("Only the coordinator reads this. Rules reach every role in its assignment.")
        company = self.orchestrator.company
        return {
            "rules": [f"#{number} {text}" for number, text in company.rules()],
            "memory_waiting": [f"#{p.id} {p.text}" for p in company.pending_memory("company")],
        }

    def outcome_record(
        self, run_id: str, ref: str, role: str, title: str = "", target: str = "", kind: str = ""
    ) -> dict[str, Any]:
        """Something was put out outside a role's own step, such as a post the person asked for in chat. It's
        tracked like any other, and credited to the role that made it, so its verdict still reaches that role."""
        if not self.coordinator:
            raise ToolError("A role records what it put out with outcome_track.")
        if not self.org.has_role(role):
            raise ToolError(f"'{role}' isn't a role here. Name the role whose work it was, such as research/analyst.")
        item = self.orchestrator.track_outcome(run_id, role, ref, title=title, target=target, kind=kind)
        if item is None:
            raise ToolError("Give a link, or a reference a person would know it by, such as 'quote 12 to Acme'.")
        return {"tracked": True, "id": item.id, "kind": item.kind, "target": item.target}

    def usage_report(self, run_id: str, invocation_id: str, input_tokens: int, output_tokens: int) -> dict[str, Any]:
        """What the client says it spent. It's kept for people to read, and limits don't rest on it."""
        role = self._role_for(run_id, invocation_id)
        tokens = max(0, input_tokens) + max(0, output_tokens)
        self.orchestrator.record(
            run_id, "usage_self_reported", {"invocation_id": invocation_id, "role": role, "tokens": tokens}
        )
        return {"recorded": True, "self_reported": True}

    # ---- memory and skills ----------------------------------------------------------------------

    def memory_recall(self, run_id: str, invocation_id: str, query: str) -> str:
        role = self._role_for(run_id, invocation_id)
        self._require_tool(run_id, role, "memory_recall")
        found = untrusted("memory", "\n".join(self.orchestrator.memory.recall(role, query)))
        self._charge(run_id, invocation_id, role, found)
        return found

    def memory_write(self, run_id: str, invocation_id: str, text: str) -> dict[str, Any]:
        role = self._role_for(run_id, invocation_id)
        self._require_tool(run_id, role, "memory_write")
        try:
            self.orchestrator.memory.write(role, text)
        except PermissionError as error:
            raise ToolError(str(error)) from error
        return {"saved": True}

    def _workspace_tool(self, run_id: str, invocation_id: str, tool: str, arguments: dict[str, Any]) -> str:
        role = self._role_for(run_id, invocation_id)
        self._require_tool(run_id, role, tool)
        context = ToolContext(
            role=role,
            allowed=(tool,),
            workspace=self.orchestrator.workspace(run_id),
            memory=self.orchestrator.memory,
            skills=self.orchestrator.skills,
        )
        try:
            output = run_tool(context, tool, arguments)
        except ToolRefused as error:
            self._violation(run_id, "TOOL_REFUSED", f"{role} used {tool}: {error}")
            raise ToolError(str(error)) from error
        self._charge(run_id, invocation_id, role, output, json.dumps(arguments, ensure_ascii=False))
        return output

    def workspace_list(self, run_id: str, invocation_id: str) -> str:
        return self._workspace_tool(run_id, invocation_id, "workspace_list", {})

    def workspace_read(self, run_id: str, invocation_id: str, path: str) -> str:
        return self._workspace_tool(run_id, invocation_id, "workspace_read", {"path": path})

    def workspace_write(self, run_id: str, invocation_id: str, path: str, content: str) -> str:
        return self._workspace_tool(run_id, invocation_id, "workspace_write", {"path": path, "content": content})

    def skill_search(self, run_id: str, invocation_id: str, query: str) -> list[dict[str, Any]]:
        role = self._role_for(run_id, invocation_id)
        self._require_tool(run_id, role, "skill_search")
        found: list[dict[str, Any]] = self.orchestrator.skills.search(role, query)
        return found

    def skill_get(self, run_id: str, invocation_id: str, skill_id: str) -> str:
        role = self._role_for(run_id, invocation_id)
        self._require_tool(run_id, role, "skill_get")
        text = self.orchestrator.skills.get(role, skill_id)
        if text is None:
            raise ToolError("That skill doesn't exist, or isn't available to this role.")
        task_id = self._invocation(run_id, invocation_id).task_id
        self.orchestrator.skill_used(run_id, task_id, role, skill_id)
        found = untrusted("skill", text)
        self._charge(run_id, invocation_id, role, found)
        return found


class McpAdapter:
    """Pull mode: a client asks for work and submits it, so this adapter delivers nothing itself."""

    name = "mcp"
    mode: Mode = "pull"

    def __init__(self, service: McpService) -> None:
        self.service = service

    def capabilities(self) -> Capabilities:
        # The client's own tools are outside this server's control, so tool limits can't be enforced here.
        return Capabilities(attests_identity=self.service.bound_role is not None, enforces_tools=False)

    def provision(self, org: Org, home: Any) -> ProvisionReport:
        return ProvisionReport(notes=["Add 'trypro mcp' to your MCP client's servers. See the adapter's README."])

    def verify(self) -> VerifyReport:
        ok = self.service.org.has_role(self.service.org.root_role)
        return VerifyReport(ok=ok, checks=[("root role loads", ok, self.service.org.root_role)])

    def deliver(self, action: InvokeRole, run_id: str) -> Delivery:
        return Delivery()

    def collect(self, run_id: str) -> list[Any]:
        return []
