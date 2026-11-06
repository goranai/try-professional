"""What the MCP server does, separate from the protocol, so it can be tested directly.

A client drives the pipeline: ask for the next action, do it, submit the artifact. The pipeline
still enforces order, schemas, and gates. Two identity modes:

    bound:    the server was started for one role. Submissions are recorded as attested by the runtime.
    operator: one client plays whichever role an open action names, and it's recorded as "claimed".
              Order, schemas, and gates still hold, but separation between roles is only procedural.

A role is never a tool argument. It comes from how the server started, or from the open
invocation. Nothing here lets a client approve a skill, admit one, or answer for a person.
"""

from __future__ import annotations

from typing import Any

from ...core.artifacts import Flags
from ...core.audit import read_records
from ...core.orchestrator import Orchestrator
from ...core.pipeline import (
    AskHuman,
    EscalationRaised,
    Finish,
    HandoffRaised,
    IllegalEvent,
    InvokeRole,
    Purpose,
    Submission,
    UsageRecorded,
)
from ...org import Org
from ..api_runner.prompts import system_prompt, untrusted, user_prompt
from ..api_runner.runner import TOOLS_BY_PURPOSE
from ..api_runner.tools import submit_spec
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
    "memory_promote",
    "skill_search",
    "skill_get",
    "usage_report",
    "audit_tail",
)
DEFAULT_AUDIT_TAIL = 20
MAX_AUDIT_TAIL = 200


class ToolError(Exception):
    """A refusal the client should see and can act on."""


class McpService:
    def __init__(self, orchestrator: Orchestrator, *, bound_role: str | None = None) -> None:
        if bound_role is not None and not orchestrator.org.has_role(bound_role):
            raise ValueError(f"'{bound_role}' isn't a role in this company")
        self.orchestrator = orchestrator
        self.org: Org = orchestrator.org
        self.bound_role = bound_role

    @property
    def attestation(self) -> Any:
        return "runtime" if self.bound_role else "claimed"

    # ---- identity ------------------------------------------------------------------------

    def _role_for(self, run_id: str, invocation_id: str) -> str:
        """The acting role: fixed at launch when bound, and otherwise the role this work was assigned to."""
        invocation = self._invocation(run_id, invocation_id)
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
            "identity": self.bound_role or "operator",
            "departments": [
                {"id": d.id, "head": d.head_ref, "activation": d.activation, "roles": [f"{d.id}/{r}" for r in d.roles]}
                for d in self.org.departments.values()
            ],
        }

    def run_status(self, run_id: str) -> dict[str, Any]:
        state = self.orchestrator.state(run_id)
        final = state.artifacts.get(state.final_artifact_id or "")
        return {
            "run_id": run_id,
            "phase": state.phase.value,
            "goal": state.goal,
            "activated_departments": list(state.activated),
            "tasks": [{"id": t.task_id, "status": t.status.value, "owner": t.owner_role} for t in state.tasks.values()],
            "tokens_self_reported": state.usage.total,
            "counters": state.counters.model_dump(),
            "final": final.payload if final and state.phase.value == "done" else None,
        }

    def audit_tail(self, run_id: str, count: int = DEFAULT_AUDIT_TAIL) -> list[dict[str, Any]]:
        limit = max(1, min(count, MAX_AUDIT_TAIL))
        records = read_records(self.orchestrator.audit_path(run_id))[-limit:]
        return [{k: record[k] for k in ("seq", "ts", "kind", "data")} for record in records]

    # ---- the loop ---------------------------------------------------------------------------

    def run_start(self, goal: str) -> dict[str, Any]:
        if self.bound_role and self.bound_role != self.org.root_role:
            raise ToolError(f"Only {self.org.root_role} starts a run. This server acts as {self.bound_role}.")
        run_id, _ = self.orchestrator.start(goal)
        return {"run_id": run_id, **self.run_next_action(run_id)}

    def run_next_action(self, run_id: str) -> dict[str, Any]:
        actions = self.orchestrator.open_actions(run_id)
        finish = next((a for a in actions if isinstance(a, Finish)), None)
        if finish is not None:
            return {
                "status": finish.status,
                "reason": finish.reason,
                "actions": [],
                "final": self.run_status(run_id)["final"],
            }
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
                    }
                    for q in questions
                ],
            }
        work = [a for a in actions if isinstance(a, InvokeRole)]
        if self.bound_role:
            work = [a for a in work if a.role == self.bound_role]
        return {"status": "working", "actions": [self._describe(run_id, action) for action in work]}

    def _describe(self, run_id: str, action: InvokeRole) -> dict[str, Any]:
        state = self.orchestrator.state(run_id)
        invocation = state.invocations[action.invocation_id]
        role = self.org.role(action.role)
        declares_flags = action.purpose == Purpose.EXECUTE
        skills = self.orchestrator.skills
        loadout = skills.loadout(role.ref) if declares_flags else []
        usable = [t for t in role.allowed_tools if t in TOOLS_BY_PURPOSE[action.purpose] and t in MCP_TOOLS]
        return {
            "invocation_id": action.invocation_id,
            "role": action.role,
            "purpose": action.purpose.value,
            "output_type": action.output_type,
            "task_id": action.task_id,
            "gate": action.gate,
            "instructions": system_prompt(role, self.org),
            "assignment": user_prompt(
                invocation, state, self.org, None, loadout, briefing=self.orchestrator.briefing(run_id, invocation)
            ),
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
    ) -> dict[str, Any]:
        role = self._role_for(run_id, invocation_id)
        try:
            parsed_flags = Flags.model_validate(flags or {})
        except ValueError as error:
            return {"accepted": False, "code": "REJECTED", "reason": str(error)}
        event = Submission(
            invocation_id=invocation_id,
            actor=role,
            attested_by=self.attestation,
            payload=payload,
            flags=parsed_flags,
            language=language,
            created_at=self.orchestrator.clock(),
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

    def usage_report(self, run_id: str, invocation_id: str, input_tokens: int, output_tokens: int) -> dict[str, Any]:
        """Tokens per run are tracked, but an MCP server can't see them, so the client reports its own."""
        role = self._role_for(run_id, invocation_id)
        self.orchestrator.handle(
            run_id,
            UsageRecorded(
                invocation_id=invocation_id,
                role=role,
                model="self-reported",
                input_tokens=max(0, input_tokens),
                output_tokens=max(0, output_tokens),
            ),
        )
        return {"recorded": True, "self_reported": True}

    # ---- memory and skills ----------------------------------------------------------------------

    def memory_recall(self, run_id: str, invocation_id: str, query: str) -> str:
        role = self._role_for(run_id, invocation_id)
        self._require_tool(run_id, role, "memory_recall")
        return untrusted("memory", "\n".join(self.orchestrator.memory.recall(role, query)))

    def memory_write(self, run_id: str, invocation_id: str, text: str) -> dict[str, Any]:
        role = self._role_for(run_id, invocation_id)
        self._require_tool(run_id, role, "memory_write")
        try:
            self.orchestrator.memory.write(role, text)
        except PermissionError as error:
            raise ToolError(str(error)) from error
        return {"saved": True}

    def memory_promote(self, run_id: str, invocation_id: str, text: str, source: str) -> dict[str, Any]:
        role = self._role_for(run_id, invocation_id)
        try:
            self.orchestrator.memory.promote(role, text, source)
        except PermissionError as error:
            self._violation(run_id, "PROMOTE_REFUSED", str(error))
            raise ToolError(str(error)) from error
        return {"saved": True}

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
        return untrusted("skill", text)


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
