"""Estimate tokens for a typical run of a company, with no model call.

It replays a typical run with the scripted model and adds up this company's real prompts, then scales
by the gap between prompts and total tokens in a measured run. That gap comes from ONE measured run
of three roles, so treat this as a rough range. docs/architecture.md says how far off it has been.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..adapters.api_runner.clients import ChatRequest, ChatResponse
from ..adapters.api_runner.runner import ApiRunner, ModelRoutes
from ..adapters.api_runner.scripted import ScriptedClient, _work_output
from ..core.orchestrator import Orchestrator
from ..core.pipeline import Purpose
from ..org import Org

CHARS_PER_TOKEN = 4
# Measured on 2026-11-06, deepseek-v4.1-flash-1017, default org, memory on: 31,360 real tokens, against
# 7,005 estimated from prompts alone (scripts/measure_prompts.py). Real runs add replies, retries, and tools.
REAL_TO_PROMPT_RATIO = 31_360 / 7_005
LOW_FACTOR = 0.7
HIGH_FACTOR = 1.5
SUPPORT_DEPARTMENTS = ("legal", "qa")
_GOAL = "A typical goal for this company, using each department that does the work once."


@dataclass(frozen=True)
class Estimate:
    low: int
    high: int
    prompt_tokens: int
    steps: int
    tasks: int
    ceiling: int | None

    @property
    def may_hit_ceiling(self) -> bool:
        return self.ceiling is not None and self.high >= self.ceiling

    def to_dict(self) -> dict[str, Any]:
        return {
            "low": self.low,
            "high": self.high,
            "prompt_tokens": self.prompt_tokens,
            "steps": self.steps,
            "tasks": self.tasks,
            "ceiling": self.ceiling,
        }


class _Typical(ScriptedClient):
    """One work package per working department, and one task per working role."""

    def __init__(self) -> None:
        super().__init__(self._override)
        self.chars = 0
        self.steps = 0

    def _override(self, invocation: Any, state: Any, org: Org) -> dict[str, Any] | None:
        root = org.root_role.split("/")[0]
        working = [d for d in org.departments.values() if d.id != root and d.id not in SUPPORT_DEPARTMENTS]
        if invocation.purpose == Purpose.PLAN:
            ids = [f"wp-{d.id}" for d in working]
            return {
                "activated_departments": [{"id": d.id, "reason": "Typical run"} for d in working],
                "work_packages": [
                    {
                        "id": ids[n],
                        "title": f"{d.name or d.id} work",
                        "owner_department": d.id,
                        "depends_on": ids[:n][-1:],
                        "acceptance": ["Reviewed and approved"],
                    }
                    for n, d in enumerate(working)
                ],
            }
        if invocation.purpose == Purpose.DISPATCH:
            package = state.packages[invocation.work_package_id]
            department = org.departments[package.owner_department]
            members = [r for r in department.roles if r != department.head] or [department.head]
            return {
                "work_package_id": package.id,
                "tasks": [
                    {
                        "id": f"t{n}",
                        "title": f"{member} work",
                        "owner_role": f"{department.id}/{member}",
                        "goal": "A typical task's goal, in one sentence.",
                        "expected_output": "An artifact that meets the definition of done.",
                        "definition_of_done": ["Done as described"],
                        "output_type": _work_output(org.role(f"{department.id}/{member}").outputs),
                    }
                    for n, member in enumerate(members)
                ],
            }
        return None

    def complete(self, request: ChatRequest) -> ChatResponse:
        tools = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in request.tools]
        self.chars += len(request.system) + len(json.dumps(tools, ensure_ascii=False, separators=(",", ":")))
        self.chars += sum(len(str(m.get("content", ""))) for m in request.messages if isinstance(m, dict))
        self.steps += 1
        return super().complete(request)


def estimate_run(org: Org) -> Estimate:
    client = _Typical()
    with tempfile.TemporaryDirectory(prefix="trypro-estimate-") as work:
        orchestrator = Orchestrator(org, Path(work))
        try:
            runner = ApiRunner(orchestrator, client, ModelRoutes({"standard": "scripted"}))
            run = runner.run(_GOAL)
            all_tasks = orchestrator.state(run.run_id).tasks.values()
            tasks = sum(1 for task in all_tasks if task.output_type != "FinalDeliverable")
        finally:
            orchestrator.close()
    prompt_tokens = client.chars // CHARS_PER_TOKEN
    middle = prompt_tokens * REAL_TO_PROMPT_RATIO
    return Estimate(
        low=_rounded(middle * LOW_FACTOR),
        high=_rounded(middle * HIGH_FACTOR),
        prompt_tokens=prompt_tokens,
        steps=client.steps,
        tasks=tasks,
        ceiling=org.pipeline.max_tokens_per_run,
    )


def _rounded(value: float) -> int:
    return int(round(value, -3))
