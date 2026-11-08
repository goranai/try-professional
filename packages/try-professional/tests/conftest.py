from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

# A busy machine must not make the tests wait for memory. test_resources.py turns the check back on.
os.environ["TRYPRO_MEMORY_CHECK"] = "off"
# A test that starts an MCP server must not leave a real embedding service running behind it.
os.environ["TRYPRO_EMBED_SERVICE"] = "off"

from try_professional.adapters.api_runner.clients import ChatRequest, ChatResponse, ToolCall, ToolResult
from try_professional.adapters.api_runner.runner import ApiRunner, ModelRoutes
from try_professional.adapters.api_runner.scripted import ScriptedClient
from try_professional.core.audit import read_records
from try_professional.core.orchestrator import Orchestrator
from try_professional.org import Org, load_org
from try_professional.packs import available_packs, install_org

ROUTES = ModelRoutes({"standard": "test-model", "executive": "test-model", "fast": "test-model"})


@pytest.fixture
def make_org(tmp_path: Path) -> Callable[..., Org]:
    counter = {"n": 0}

    def _make(packs: list[str] | None = None, mutate: Callable[[Path], None] | None = None) -> Org:
        counter["n"] += 1
        org_dir = tmp_path / f"org{counter['n']}"
        install_org(org_dir, "Test Co", packs if packs is not None else available_packs())
        if mutate:
            mutate(org_dir)
        return load_org(org_dir)

    return _make


@pytest.fixture
def org(make_org: Callable[..., Org]) -> Org:
    return make_org()


class RecordingMemory:
    def __init__(self, hits: list[str] | None = None) -> None:
        self.hits = hits or []
        self.recalls: list[str] = []
        self.writes: list[str] = []
        self.promoted: list[tuple[str, str, str]] = []

    def recall(self, role: str, query: str) -> list[str]:
        self.recalls.append(role)
        return self.hits

    def write(self, role: str, text: str) -> None:
        self.writes.append(role)

    def promote(self, role: str, text: str, source: str) -> None:
        self.promoted.append((role, text, source))


class RecordingSkills:
    def __init__(self) -> None:
        self.searches: list[str] = []

    def search(self, role: str, query: str) -> list[dict[str, Any]]:
        self.searches.append(role)
        return []

    def get(self, role: str, skill_id: str) -> str | None:
        return None

    def loadout(self, role: str) -> list[dict[str, Any]]:
        return []

    def available(self, role: str) -> bool:
        return False

    def proposable(self, role: str) -> bool:
        return False

    def propose(self, proposer: str, skill_id: str, for_role: str, reason: str) -> None:
        return None


class Harness:
    """A run wired to a scripted model, with everything recorded."""

    def __init__(self, org: Org, home: Path, override: Any = None, memory: Any = None) -> None:
        self.memory = memory or RecordingMemory()
        self.skills = RecordingSkills()
        self.orchestrator = Orchestrator(org, home, memory=self.memory, skills=self.skills)
        self.client = ScriptedClient(override)
        self.runner = ApiRunner(self.orchestrator, self.client, ROUTES)

    def records(self, run_id: str) -> list[dict[str, Any]]:
        return read_records(self.orchestrator.audit_path(run_id))

    def called_roles(self) -> set[str]:
        return {c["role"] for c in self.client.calls}


@pytest.fixture
def harness(tmp_path: Path) -> Callable[..., Harness]:
    counter = {"n": 0}

    def _make(org: Org, override: Any = None, memory: Any = None) -> Harness:
        counter["n"] += 1
        return Harness(org, tmp_path / f"home{counter['n']}", override, memory)

    return _make


class SequenceClient:
    """Plays back scripted responses in order. Records every request it receives."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.requests: list[ChatRequest] = []

    def user_message(self, text: str) -> Any:
        return {"role": "user", "content": text}

    def tool_results_messages(self, results: list[ToolResult]) -> list[Any]:
        return [{"role": "tool", "call_id": r.call_id, "content": r.content, "is_error": r.is_error} for r in results]

    def complete(self, request: ChatRequest) -> ChatResponse:
        # The runner keeps appending to this list, so keep a copy of what was actually sent.
        self.requests.append(replace(request, messages=list(request.messages)))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def tool_response(name: str, arguments: dict[str, Any], call_id: str = "c1") -> ChatResponse:
    return ChatResponse(
        text="",
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        stop_reason="tool_use",
        assistant_message={"role": "assistant", "tool": name},
        input_tokens=10,
        output_tokens=5,
        model="test-model",
    )


def text_response(text: str, stop_reason: str = "end", detail: str = "") -> ChatResponse:
    return ChatResponse(
        text=text,
        tool_calls=[],
        stop_reason=stop_reason,
        assistant_message={"role": "assistant", "content": text},
        input_tokens=10,
        output_tokens=5,
        model="test-model",
        detail=detail,
    )


SMALL_LIMIT = 120_000
LARGE_LIMIT = 300_000


def with_limits(org_dir: Path, small: int = SMALL_LIMIT, large: int = LARGE_LIMIT) -> None:
    """Token limits are off by default. Tests of what happens at one set one here."""
    import yaml

    path = org_dir / "org.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    budget = data.setdefault("pipeline", {}).setdefault("budget", {})
    budget.update(max_tokens_per_run=large, max_tokens_per_small_command=small)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
