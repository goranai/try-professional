"""Measure what each pipeline step sends to a model. Offline: uses the scripted model, and costs nothing.

Sizes are characters, and tokens are estimated at 4 characters each. Multilingual text and
real model replies differ, so use this to compare versions of the prompts, not to predict a bill.
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from try_professional.adapters.api_runner.clients import ChatRequest, ChatResponse
from try_professional.adapters.api_runner.runner import ApiRunner, ModelRoutes
from try_professional.adapters.api_runner.scripted import ScriptedClient
from try_professional.core.orchestrator import Orchestrator
from try_professional.org import load_org
from try_professional.packs import available_packs, install_org

GOAL = "Write a short three-step plan for adding a health check endpoint to our web service."


@dataclass
class StepSize:
    purpose: str
    role: str
    system: int
    user: int
    tools: int

    @property
    def total(self) -> int:
        return self.system + self.user + self.tools


class MeasuringClient(ScriptedClient):
    def __init__(self) -> None:
        super().__init__()
        self.steps: list[StepSize] = []

    def complete(self, request: ChatRequest) -> ChatResponse:
        invocation = request.metadata["invocation"]
        tools = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in request.tools]
        self.steps.append(
            StepSize(
                purpose=invocation.purpose.value,
                role=invocation.role,
                system=len(request.system),
                user=sum(len(str(m.get("content", ""))) for m in request.messages if isinstance(m, dict)),
                tools=len(json.dumps(tools, ensure_ascii=False, separators=(",", ":"))),
            )
        )
        return super().complete(request)


class FullLoadout:
    """A role with a full loadout of realistic skill IDs and descriptions, cut as they really are."""

    def __init__(self, size: int) -> None:
        self.items = [
            {
                "skill_id": f"some-owner/agent-skills/skills/skill-number-{n}",
                "description": ("Write and review the kind of thing this skill is about, in clear steps. " * 2)[:70],
            }
            for n in range(size)
        ]

    def search(self, role: str, query: str) -> list[dict[str, Any]]:
        return []

    def get(self, role: str, skill_id: str) -> str | None:
        return None

    def loadout(self, role: str) -> list[dict[str, Any]]:
        return self.items

    def available(self, role: str) -> bool:
        return True

    def proposable(self, role: str) -> bool:
        return False

    def propose(self, proposer: str, skill_id: str, for_role: str, reason: str) -> None:
        return None


def measure(factory: Callable[[], MeasuringClient] = MeasuringClient, *, loadout_size: int = 0) -> list[StepSize]:
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        install_org(root / "org", "Measure", available_packs())
        skills = FullLoadout(loadout_size) if loadout_size else None
        orchestrator = Orchestrator(load_org(root / "org"), root / "home", skills=skills)
        client = factory()
        routes = ModelRoutes({"standard": "m", "executive": "m", "fast": "m"})
        result = ApiRunner(orchestrator, client, routes).run(GOAL)
        orchestrator.close()
        if result.status != "done":
            raise RuntimeError(f"measuring run ended as {result.status}")
        return client.steps


def main() -> None:
    size = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    steps = measure(loadout_size=size)
    print(f"{'step':<14} {'system':>7} {'user':>7} {'tools':>7} {'~tokens':>8}")
    for step in steps:
        print(f"{step.purpose:<14} {step.system:>7} {step.user:>7} {step.tools:>7} {step.total // 4:>8}")
    total = sum(s.total for s in steps)
    print(f"{'total':<14} {'':>7} {'':>7} {'':>7} {total // 4:>8}")
    summary: dict[str, Any] = {"steps": [asdict(s) for s in steps], "total_chars": total}
    print(json.dumps({"total_chars": summary["total_chars"]}))


if __name__ == "__main__":
    main()
