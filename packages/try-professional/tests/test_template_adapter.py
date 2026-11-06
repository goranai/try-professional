"""The template must really work, because people will copy it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from try_professional.adapters.api_runner.scripted import default_payload
from try_professional.adapters.base import RuntimeAdapter
from try_professional.adapters.template import TemplateAdapter
from try_professional.core.orchestrator import Orchestrator
from try_professional.core.pipeline import InvokeRole
from try_professional.org import Org


def _stand_in(orchestrator: Orchestrator, run_holder: dict[str, str]) -> Any:
    def call_runtime(instructions: str, assignment: str, action: InvokeRole) -> dict[str, Any]:
        assert instructions and assignment
        state = orchestrator.state(run_holder["id"])
        return default_payload(state.invocations[action.invocation_id], state, orchestrator.org)

    return call_runtime


def test_it_implements_the_interface(org: Org, tmp_path: Path) -> None:
    adapter = TemplateAdapter(Orchestrator(org, tmp_path), lambda s, a, x: {})
    assert isinstance(adapter, RuntimeAdapter)
    assert adapter.verify().ok


def test_it_runs_a_whole_company(org: Org, tmp_path: Path) -> None:
    orchestrator = Orchestrator(org, tmp_path)
    holder: dict[str, str] = {}
    original = orchestrator.start

    def start(goal: str, run_id: str | None = None) -> Any:
        result = original(goal, run_id)
        holder["id"] = result[0]
        return result

    orchestrator.start = start  # type: ignore[method-assign]
    result = TemplateAdapter(orchestrator, _stand_in(orchestrator, holder)).run("Build a thing")
    assert result["status"] == "done"
    assert orchestrator.verify_audit(result["run_id"]).ok


def test_a_runtime_failure_is_retried_then_reaches_a_person(org: Org, tmp_path: Path) -> None:
    def broken(instructions: str, assignment: str, action: InvokeRole) -> dict[str, Any]:
        raise RuntimeError("runtime is down")

    result = TemplateAdapter(Orchestrator(org, tmp_path), broken).run("Build a thing")
    assert result["status"] == "blocked"
