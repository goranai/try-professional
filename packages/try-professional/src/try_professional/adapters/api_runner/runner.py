"""Push-mode adapter: calls the model directly and drives a run to its end."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ...core.artifacts import ArtifactError, Flags, parse_payload
from ...core.orchestrator import Orchestrator
from ...core.pipeline import (
    AskHuman,
    EscalationRaised,
    Finish,
    HandoffRaised,
    HumanDecided,
    IllegalEvent,
    InvocationFailed,
    InvokeRole,
    Purpose,
    RunPhase,
    Submission,
    UsageRecorded,
)
from ...org import Org, Role
from ..base import Capabilities, Delivery, Mode, ProvisionReport, VerifyReport
from .clients import ChatClient, ChatRequest, ClientError, ToolResult
from .prompts import system_prompt, user_prompt
from .tools import CONTROL_TOOLS, ESCALATE, HANDOFF, SUBMIT, ToolContext, ToolRefused, run_tool, tools_for

HumanHandler = Callable[[AskHuman], "HumanDecided | None"]
DEFAULT_MAX_TURNS = 12
MAX_NUDGES = 2

_READ = frozenset({"workspace_read", "workspace_list"})
_MEMORY = frozenset({"memory_recall"})
# A role keeps only the tools its current step can use. The role file is still the upper limit.
TOOLS_BY_PURPOSE: dict[Purpose, frozenset[str]] = {
    Purpose.INTAKE: frozenset(),
    Purpose.PLAN: _MEMORY,
    Purpose.DISPATCH: _MEMORY,
    Purpose.EXECUTE: _READ | _MEMORY | {"workspace_write", "memory_write", "skill_search"},
    Purpose.REVIEW: _READ,
    Purpose.ESCALATION: _MEMORY,
    Purpose.INTEGRATE: _READ,
    Purpose.RETROSPECTIVE: frozenset(),
}


@dataclass
class ModelRoutes:
    """Maps a role's model_tier to a model name."""

    tiers: dict[str, str]

    def for_tier(self, tier: str) -> str:
        return self.tiers.get(tier) or self.tiers["standard"]


class Router(Protocol):
    """Decides which client and model serve a role."""

    def route(self, role: Role) -> tuple[ChatClient, str]: ...


@dataclass
class StaticRouter:
    """One client for everyone, with the model chosen by tier."""

    client: ChatClient
    routes: ModelRoutes

    def route(self, role: Role) -> tuple[ChatClient, str]:
        return self.client, self.routes.for_tier(role.model_tier)


@dataclass
class RunResult:
    run_id: str
    status: str  # done | failed | blocked
    final: dict[str, Any] | None = None
    reason: str | None = None
    questions: list[AskHuman] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    calls: int = 0


class ApiRunner:
    name = "api_runner"
    mode: Mode = "push"

    def __init__(
        self,
        orchestrator: Orchestrator,
        client: ChatClient | None = None,
        routes: ModelRoutes | None = None,
        *,
        router: Router | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        on_human: HumanHandler | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.org: Org = orchestrator.org
        if router is None:
            if client is None or routes is None:
                raise ValueError("ApiRunner needs a router, or a client and routes")
            router = StaticRouter(client, routes)
        self.router = router
        self.routes = routes
        self.max_turns = max_turns
        self.on_human = on_human

    # ---- RuntimeAdapter ---------------------------------------------------------

    def capabilities(self) -> Capabilities:
        return Capabilities(
            attests_identity=True,
            enforces_tools=True,
            max_parallel=self.org.pipeline.max_parallel_tasks,
        )

    def provision(self, org: Org, home: Path) -> ProvisionReport:
        return ProvisionReport(notes=["The API runner needs no provisioning."])

    def verify(self) -> VerifyReport:
        report = VerifyReport(ok=True)
        root_ok = self.org.has_role(self.org.root_role)
        report.checks.append(("root role loads", root_ok, self.org.root_role))
        tiers_ok = self.routes is None or bool(self.routes.tiers.get("standard"))
        report.checks.append(("a model is set for tier 'standard'", tiers_ok, ""))
        report.ok = root_ok and tiers_ok
        return report

    def deliver(self, action: InvokeRole, run_id: str) -> Delivery:
        return Delivery(events=self._invoke(action, run_id))

    def collect(self, run_id: str) -> Iterable[Any]:
        return []

    # ---- driving a run ------------------------------------------------------------

    def run(self, goal: str, run_id: str | None = None) -> RunResult:
        run_id, _ = self.orchestrator.start(goal, run_id)
        return self.resume(run_id)

    def resume(self, run_id: str) -> RunResult:
        """Run until finished or blocked. Works after a crash: open work is read from the ledger."""
        limit = self.org.pipeline.max_parallel_tasks
        in_flight: dict[Future[list[Any]], str] = {}
        with ThreadPoolExecutor(max_workers=limit, thread_name_prefix="trypro") as pool:
            while True:
                actions = self.orchestrator.open_actions(run_id)
                busy = set(in_flight.values())
                for action in actions:
                    if isinstance(action, InvokeRole) and action.invocation_id not in busy and len(in_flight) < limit:
                        in_flight[pool.submit(self._invoke, action, run_id)] = action.invocation_id
                        busy.add(action.invocation_id)

                if not in_flight:
                    finish = next((a for a in actions if isinstance(a, Finish)), None)
                    if finish is not None:
                        return self._result(run_id, finish.status)
                    questions = [a for a in actions if isinstance(a, AskHuman)]
                    if questions:
                        if self._answer(run_id, questions):
                            continue
                        return self._result(run_id, "blocked", questions)
                    return self._result(run_id, "blocked", reason="no actions are available")

                done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for future in done:
                    in_flight.pop(future)
                    for event in future.result():
                        self._send(run_id, event)

    def _answer(self, run_id: str, questions: list[AskHuman]) -> bool:
        if self.on_human is None:
            return False
        answered = False
        for question in questions:
            decision = self.on_human(question)
            if decision is not None:
                self._send(run_id, decision)
                answered = True
        return answered

    def _send(self, run_id: str, event: Any) -> None:
        try:
            self.orchestrator.handle(run_id, event)
        except IllegalEvent as error:
            # Already audited as a violation. Late results from a finished run land here.
            if error.code == "RUN_FINISHED":
                return
            invocation_id = getattr(event, "invocation_id", None)
            if invocation_id and error.code != "NO_OPEN_INVOCATION":
                self.orchestrator.handle(
                    run_id, InvocationFailed(invocation_id=invocation_id, reason=str(error), retryable=True)
                )

    def _result(
        self, run_id: str, status: str, questions: list[AskHuman] | None = None, reason: str | None = None
    ) -> RunResult:
        state = self.orchestrator.state(run_id)
        final = state.artifacts.get(state.final_artifact_id or "")
        return RunResult(
            run_id=run_id,
            status=status,
            final=final.payload if final and status == "done" else None,
            reason=reason or state.failure_reason,
            questions=questions or [],
            input_tokens=state.usage.input_tokens,
            output_tokens=state.usage.output_tokens,
            cached_input_tokens=state.usage.cached_input_tokens,
            calls=state.usage.calls,
        )

    # ---- one role, one assignment ---------------------------------------------------

    def _invoke(self, action: InvokeRole, run_id: str) -> list[Any]:
        state = self.orchestrator.state(run_id)
        invocation = state.invocations.get(action.invocation_id)
        if invocation is None:
            return []
        role = self.org.role(action.role)
        may_handoff = self.org.is_head(role.ref) and role.ref != self.org.root_role
        may_handoff = may_handoff and action.purpose in (Purpose.DISPATCH, Purpose.EXECUTE)
        usable = tuple(t for t in role.allowed_tools if t in TOOLS_BY_PURPOSE[action.purpose])
        tools = tools_for(
            usable, action.output_type, is_head=may_handoff, declares_flags=action.purpose == Purpose.EXECUTE
        )
        context = ToolContext(
            role=role.ref,
            allowed=usable,
            workspace=self.orchestrator.workspace(run_id),
            memory=self.orchestrator.memory,
            skills=self.orchestrator.skills,
        )
        system = system_prompt(role, self.org)
        try:
            client, model = self.router.route(role)
        except ClientError as error:
            return [InvocationFailed(invocation_id=action.invocation_id, reason=str(error), retryable=error.retryable)]
        workspace = context.workspace.resolve()
        files = sorted(str(p.relative_to(workspace)).replace("\\", "/") for p in workspace.rglob("*") if p.is_file())
        messages = [client.user_message(user_prompt(invocation, state, self.org, files))]
        metadata = {"invocation": invocation, "state": state, "org": self.org}
        nudges = 0

        for _ in range(self.max_turns):
            # Stop spending as soon as the run is blocked. The invocation stays open and
            # is delivered again if a human lets the run continue.
            if self.orchestrator.state(run_id).phase == RunPhase.BLOCKED:
                return []
            try:
                response = client.complete(
                    ChatRequest(system=system, messages=messages, tools=tools, model=model, metadata=metadata)
                )
            except ClientError as error:
                return [
                    InvocationFailed(invocation_id=action.invocation_id, reason=str(error), retryable=error.retryable)
                ]

            self._send(
                run_id,
                UsageRecorded(
                    invocation_id=action.invocation_id,
                    model=response.model,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cached_input_tokens=response.cached_input_tokens,
                    role=role.ref,
                ),
            )

            if response.stop_reason == "refusal":
                detail = f" ({response.detail})" if response.detail else ""
                return [
                    InvocationFailed(
                        invocation_id=action.invocation_id, reason=f"The model refused{detail}.", retryable=False
                    )
                ]

            messages.append(response.assistant_message)

            if not response.tool_calls:
                if response.stop_reason == "max_tokens":
                    return [
                        InvocationFailed(
                            invocation_id=action.invocation_id,
                            reason="The output hit the token limit before an artifact was submitted.",
                            retryable=True,
                        )
                    ]
                nudges += 1
                if nudges > MAX_NUDGES:
                    return [
                        InvocationFailed(
                            invocation_id=action.invocation_id,
                            reason="The model never called submit_artifact.",
                            retryable=True,
                        )
                    ]
                messages.append(
                    client.user_message(
                        f"You must finish by calling {SUBMIT} with a {action.output_type}, or call {ESCALATE}."
                    )
                )
                continue

            results: list[ToolResult] = []
            control_event: Any = None
            for call in response.tool_calls:
                if call.parse_error:
                    results.append(ToolResult(call.id, call.parse_error, is_error=True))
                    continue
                if call.name in CONTROL_TOOLS:
                    if control_event is not None:
                        results.append(ToolResult(call.id, "Only one control call is allowed.", is_error=True))
                        continue
                    outcome = self._control(call.name, call.arguments, action, run_id)
                    if isinstance(outcome, str):
                        results.append(ToolResult(call.id, outcome, is_error=True))
                    else:
                        control_event = outcome
                        results.append(ToolResult(call.id, "Accepted."))
                    continue
                try:
                    output = run_tool(context, call.name, call.arguments)
                    self.orchestrator.record(
                        run_id,
                        "tool_call",
                        {"invocation_id": action.invocation_id, "role": role.ref, "tool": call.name},
                    )
                    results.append(ToolResult(call.id, output))
                except ToolRefused as error:
                    self.orchestrator.record(
                        run_id,
                        "violation",
                        {"code": "TOOL_REFUSED", "role": role.ref, "tool": call.name, "message": str(error)},
                    )
                    results.append(ToolResult(call.id, str(error), is_error=True))
            if control_event is not None:
                return [control_event]
            messages.extend(client.tool_results_messages(results))

        return [
            InvocationFailed(
                invocation_id=action.invocation_id,
                reason=f"No artifact after {self.max_turns} turns.",
                retryable=False,
            )
        ]

    def _control(self, name: str, arguments: dict[str, Any], action: InvokeRole, run_id: str) -> Any:
        """Return an event, or an error string for the model to correct.

        Schema errors go straight back to the model, which is cheaper than failing the
        whole invocation. Hierarchy rules are still enforced by the pipeline.
        """
        if name == SUBMIT:
            payload = arguments.get("payload")
            try:
                parse_payload(action.output_type, payload)
                flags = Flags.model_validate(arguments.get("flags") or {})
            except (ArtifactError, ValueError) as error:
                self._count_invalid(run_id, action, str(error))
                return f"Rejected, fix this and call {SUBMIT} again: {error}"
            return Submission(
                invocation_id=action.invocation_id,
                actor=action.role,
                payload=payload,
                flags=flags,
                language=arguments.get("language"),
                created_at=self.orchestrator.clock(),
            )
        if name == ESCALATE:
            return EscalationRaised(invocation_id=action.invocation_id, actor=action.role, payload=arguments)
        if name == HANDOFF:
            if not self.org.is_head(action.role):
                self.orchestrator.record(
                    run_id,
                    "violation",
                    {"code": "HANDOFF_NOT_HEAD", "role": action.role, "message": "Only heads can hand off."},
                )
                return f"{action.role} is not a department head. Call {ESCALATE} instead."
            return HandoffRaised(invocation_id=action.invocation_id, actor=action.role, payload=arguments)
        return f"Unknown control tool {name}"

    def _count_invalid(self, run_id: str, action: InvokeRole, reason: str) -> None:
        self.orchestrator.record(
            run_id, "invalid_output", {"invocation_id": action.invocation_id, "role": action.role, "reason": reason}
        )
