"""A deterministic stand-in for a model. No key, no network, no cost.

It proves the pipeline's mechanics, not that any model does good work. Output is
always clearly labelled, so dry-run results can't be mistaken for real ones.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ...core.pipeline import Purpose, RunState
from ...core.pipeline.state import Invocation
from ...org import Org
from .clients import ChatRequest, ChatResponse, ToolCall, ToolResult
from .tools import SUBMIT

LABEL = "[scripted model]"
Override = Callable[[Invocation, RunState, Org], dict[str, Any] | None]
_WORK_OUTPUTS = ("CodeChange", "PublicContent", "ResearchReport", "TaskReport")


def default_payload(invocation: Invocation, state: RunState, org: Org) -> dict[str, Any]:
    """A minimal valid artifact for any assignment."""
    purpose = invocation.purpose
    if purpose == Purpose.INTAKE:
        return {
            "original_goal": state.goal,
            "restated_goal": f"{LABEL} {state.goal}",
            "user_language": "und",
            "constraints": [],
            "success_criteria": ["The goal is met"],
        }
    if purpose == Purpose.PLAN:
        root_department = org.root_role.split("/")[0]
        others = [d.id for d in org.departments.values() if d.id != root_department]
        owner_department = "engineering" if "engineering" in others else (others[0] if others else root_department)
        return {
            "activated_departments": [{"id": owner_department, "reason": f"{LABEL} owns the work"}],
            "work_packages": [
                {
                    "id": "wp1",
                    "title": state.goal[:120],
                    "owner_department": owner_department,
                    "acceptance": ["Reviewed and approved"],
                }
            ],
        }
    if purpose == Purpose.DISPATCH:
        package = state.packages[invocation.work_package_id or ""]
        department = org.departments[package.owner_department]
        members = [r for r in department.roles if r != department.head] or [department.head]
        owner = f"{department.id}/{members[0]}"
        return {
            "work_package_id": package.id,
            "tasks": [
                {
                    "id": "t1",
                    "title": package.title,
                    "owner_role": owner,
                    "goal": f"{LABEL} {package.title}",
                    "expected_output": "An artifact that meets the definition of done",
                    "definition_of_done": ["Done as described"],
                    "output_type": _work_output(org.role(owner).outputs),
                }
            ],
        }
    if purpose == Purpose.EXECUTE:
        return _execute_payload(invocation)
    if purpose == Purpose.REVIEW:
        return {
            "gate": invocation.gate,
            "subject_artifact_id": invocation.subject_artifact_id,
            "decision": "approve",
            "issues": [],
        }
    if purpose == Purpose.ESCALATION:
        return {"decision": "resume", "guidance": f"{LABEL} continue"}
    if purpose == Purpose.INTEGRATE:
        charter = state.artifacts[state.charter_artifact_id or ""]
        return {
            "summary_in_user_language": f"{LABEL} Completed: {state.goal}",
            "artifact_refs": list(invocation.context_refs),
            "criteria_results": [
                {"criterion": c, "met": True, "evidence": f"{LABEL} no real work was done"}
                for c in charter.payload["success_criteria"]
            ],
            "known_limits": [f"{LABEL} This was a dry run. No model produced this."],
        }
    return {"lessons": [f"{LABEL} nothing to learn from a dry run"], "procedures": [], "skill_proposals": []}


def _work_output(outputs: tuple[str, ...]) -> str:
    return next((o for o in outputs if o in _WORK_OUTPUTS), "TaskReport")


def _execute_payload(invocation: Invocation) -> dict[str, Any]:
    done = [{"criterion": "Done as described", "met": True, "evidence": LABEL}]
    if invocation.output_type == "CodeChange":
        return {"summary": f"{LABEL} code", "files": [{"path": "src/app.py"}], "done_criteria_met": done}
    if invocation.output_type == "PublicContent":
        return {
            "audience": "readers",
            "channel": "web",
            "locale": "en",
            "body": f"{LABEL} copy",
            "done_criteria_met": done,
        }
    if invocation.output_type == "ResearchReport":
        return {"question": "q", "findings": [{"statement": LABEL, "confidence": "low"}], "done_criteria_met": done}
    return {"summary": f"{LABEL} done", "done_criteria_met": done}


class ScriptedClient:
    """Submits a valid artifact on the first turn. Tests override any assignment."""

    def __init__(self, override: Override | None = None) -> None:
        self.override = override
        self.calls: list[dict[str, Any]] = []

    def user_message(self, text: str) -> Any:
        return {"role": "user", "content": text}

    def tool_results_messages(self, results: list[ToolResult]) -> list[Any]:
        return [{"role": "tool", "call_id": r.call_id, "content": r.content, "is_error": r.is_error} for r in results]

    def complete(self, request: ChatRequest) -> ChatResponse:
        invocation: Invocation = request.metadata["invocation"]
        state: RunState = request.metadata["state"]
        org: Org = request.metadata["org"]
        self.calls.append(
            {"role": invocation.role, "purpose": invocation.purpose.value, "tools": [t.name for t in request.tools]}
        )
        payload: dict[str, Any] | None = self.override(invocation, state, org) if self.override else None
        arguments = payload if payload is not None and "payload" in payload else None
        if arguments is None:
            arguments = {"payload": payload if payload is not None else default_payload(invocation, state, org)}
        size = len(json.dumps(arguments, ensure_ascii=False))
        call = ToolCall(id=f"call-{len(self.calls)}", name=SUBMIT, arguments=arguments)
        return ChatResponse(
            text="",
            tool_calls=[call],
            stop_reason="tool_use",
            assistant_message={"role": "assistant", "content": arguments},
            input_tokens=len(str(request.messages[0])) // 4,
            output_tokens=size // 4,
            model="scripted",
        )
