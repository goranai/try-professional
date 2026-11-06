"""A working adapter to copy. Replace call_runtime with a call to your runtime.

This one is push mode: the adapter does the work and returns the result. For pull mode, see
adapters/mcp, and for queue mode, see adapters/hermes.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...core.orchestrator import Orchestrator
from ...core.pipeline import (
    AskHuman,
    Finish,
    InvocationFailed,
    InvokeRole,
    Submission,
)
from ...org import Org
from ..api_runner.prompts import system_prompt, user_prompt
from ..base import Capabilities, Delivery, Mode, ProvisionReport, VerifyReport

# Your runtime: given a role's instructions and its assignment, return the artifact payload as a dict.
# Raise an exception if it fails.
CallRuntime = Callable[[str, str, InvokeRole], dict[str, Any]]


class TemplateAdapter:
    name = "template"
    mode: Mode = "push"

    def __init__(self, orchestrator: Orchestrator, call_runtime: CallRuntime) -> None:
        self.orchestrator = orchestrator
        self.org: Org = orchestrator.org
        self.call_runtime = call_runtime

    def capabilities(self) -> Capabilities:
        """Say honestly what your runtime guarantees. The audit log depends on it."""
        return Capabilities(attests_identity=True, enforces_tools=False, max_parallel=1)

    def provision(self, org: Org, home: Path) -> ProvisionReport:
        """Set up whatever your runtime needs: profiles, config files, and so on."""
        return ProvisionReport(notes=["Nothing to set up."])

    def verify(self) -> VerifyReport:
        """Check the setup really works. This should fail loudly if it doesn't."""
        ok = self.org.has_role(self.org.root_role)
        return VerifyReport(ok=ok, checks=[("root role loads", ok, self.org.root_role)])

    def deliver(self, action: InvokeRole, run_id: str) -> Delivery:
        """Do one assignment, and return what happened as events."""
        state = self.orchestrator.state(run_id)
        invocation = state.invocations[action.invocation_id]
        role = self.org.role(action.role)
        try:
            payload = self.call_runtime(system_prompt(role, self.org), user_prompt(invocation, state, self.org), action)
        except Exception as error:
            failed = InvocationFailed(invocation_id=action.invocation_id, reason=str(error), retryable=True)
            return Delivery(events=[failed])
        return Delivery(events=[Submission(invocation_id=action.invocation_id, actor=action.role, payload=payload)])

    def collect(self, run_id: str) -> list[Any]:
        """For queue mode: results that arrived since last time. Push mode has none."""
        return []

    def run(self, goal: str) -> dict[str, Any]:
        """Drive a run to its end, one action at a time."""
        run_id, _ = self.orchestrator.start(goal)
        while True:
            actions = self.orchestrator.open_actions(run_id)
            finish = next((a for a in actions if isinstance(a, Finish)), None)
            if finish is not None:
                return {"run_id": run_id, "status": finish.status}
            if any(isinstance(a, AskHuman) for a in actions):
                return {"run_id": run_id, "status": "blocked"}
            for action in actions:
                if isinstance(action, InvokeRole):
                    for event in self.deliver(action, run_id).events:
                        self.orchestrator.handle(run_id, event)
                    break
