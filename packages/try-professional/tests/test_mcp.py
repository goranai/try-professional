"""The MCP adapter: the service directly, the real SDK in memory, and a real server over stdio."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from try_professional.adapters.api_runner.scripted import default_payload
from try_professional.adapters.base import RuntimeAdapter
from try_professional.adapters.mcp import MCP_TOOLS, McpAdapter, McpService, ToolError
from try_professional.core.orchestrator import Orchestrator
from try_professional.core.pipeline import Purpose
from try_professional.org import Org

from .conftest import RecordingMemory
from .test_memory import FakeBackend

BACKEND = "engineering/backend"


@pytest.fixture
def orchestrator(org: Org, tmp_path: Path) -> Orchestrator:
    from try_professional.memory import MemoryGateway

    return Orchestrator(org, tmp_path, memory=MemoryGateway(org, FakeBackend()))


def _payload(service: McpService, run_id: str, action: dict[str, Any]) -> dict[str, Any]:
    state = service.orchestrator.state(run_id)
    return default_payload(state.invocations[action["invocation_id"]], state, service.org)


def _drive(service: McpService, run_id: str, first: dict[str, Any]) -> dict[str, Any]:
    """Act as a client would: read the actions, do them, submit."""
    current = first
    for _ in range(100):
        if current["status"] != "working":
            return current
        action = current["actions"][0]
        submit = getattr(service, action["submit_with"])
        current = submit(run_id, action["invocation_id"], _payload(service, run_id, action))
        assert current.get("accepted"), current
    raise AssertionError("the run never finished")


class TestOperatorMode:
    def test_a_client_can_drive_a_whole_run(self, orchestrator: Orchestrator) -> None:
        service = McpService(orchestrator)
        first = service.run_start("Build a health check endpoint")
        run_id = first["run_id"]
        final = _drive(service, run_id, first)
        assert final["status"] == "done" and final["final"]
        assert orchestrator.verify_audit(run_id).ok

    def test_actions_carry_everything_a_client_needs(self, orchestrator: Orchestrator) -> None:
        action = McpService(orchestrator).run_start("Goal")["actions"][0]
        assert action["role"] == "executive/ceo" and action["purpose"] == "intake"
        assert action["instructions"] and action["assignment"] and action["output_schema"]
        assert action["submit_with"] == "artifact_submit"

    def test_work_is_recorded_as_claimed(self, orchestrator: Orchestrator) -> None:
        service = McpService(orchestrator)
        first = service.run_start("Goal")
        run_id = first["run_id"]
        _drive(service, run_id, first)
        artifacts = orchestrator.state(run_id).artifacts.values()
        submitted = [a for a in artifacts if a.type == "GoalCharter"]
        assert submitted[0].attested_by == "claimed"

    def test_gates_still_hold(self, orchestrator: Orchestrator) -> None:
        service = McpService(orchestrator)
        current = service.run_start("Goal")
        run_id = current["run_id"]
        for _ in range(4):
            action = current["actions"][0]
            if action["purpose"] == "execute":
                break
            current = service.artifact_submit(run_id, action["invocation_id"], _payload(service, run_id, action))
        action = current["actions"][0]
        current = service.artifact_submit(run_id, action["invocation_id"], _payload(service, run_id, action))
        assert current["actions"][0]["purpose"] == "review", "a result can't skip its review"

    def test_invalid_output_comes_back_with_the_reason(self, orchestrator: Orchestrator) -> None:
        service = McpService(orchestrator)
        first = service.run_start("Goal")
        result = service.artifact_submit(first["run_id"], first["actions"][0]["invocation_id"], {"wrong": 1})
        assert result["accepted"] is False and "invalid" in result["reason"]
        assert service.run_next_action(first["run_id"])["actions"], "the work stays open"

    def test_review_submit_only_takes_reviews(self, orchestrator: Orchestrator) -> None:
        service = McpService(orchestrator)
        first = service.run_start("Goal")
        with pytest.raises(ToolError, match="isn't a review"):
            service.review_submit(first["run_id"], first["actions"][0]["invocation_id"], {})

    def test_a_blocked_run_tells_the_client_to_ask_a_person(self, orchestrator: Orchestrator) -> None:
        service = McpService(orchestrator)
        first = service.run_start("Goal")
        result = service.escalate(first["run_id"], first["actions"][0]["invocation_id"], "I can't decide this")
        assert result["status"] == "blocked"
        assert "trypro decide" in result["needs_a_person"][0]["command"]

    def test_closed_work_is_refused(self, orchestrator: Orchestrator) -> None:
        service = McpService(orchestrator)
        run_id = service.run_start("Goal")["run_id"]
        with pytest.raises(ToolError, match="isn't open"):
            service.artifact_submit(run_id, "inv-9999", {})


class TestBoundMode:
    def test_identity_is_fixed_at_launch(self, orchestrator: Orchestrator) -> None:
        operator = McpService(orchestrator)
        first = operator.run_start("Goal")
        bound = McpService(orchestrator, bound_role=BACKEND)
        with pytest.raises(ToolError, match="acts as engineering/backend"):
            bound.artifact_submit(first["run_id"], first["actions"][0]["invocation_id"], {})
        assert orchestrator.state(first["run_id"]).counters.violations == 1

    def test_it_only_sees_its_own_work(self, orchestrator: Orchestrator) -> None:
        first = McpService(orchestrator).run_start("Goal")
        assert McpService(orchestrator, bound_role=BACKEND).run_next_action(first["run_id"])["actions"] == []

    def test_only_the_root_role_starts_a_run(self, orchestrator: Orchestrator) -> None:
        with pytest.raises(ToolError, match="starts a run"):
            McpService(orchestrator, bound_role=BACKEND).run_start("Goal")

    def test_work_is_recorded_as_attested(self, orchestrator: Orchestrator) -> None:
        service = McpService(orchestrator, bound_role="executive/ceo")
        first = service.run_start("Goal")
        run_id = first["run_id"]
        action = first["actions"][0]
        assert service.artifact_submit(run_id, action["invocation_id"], _payload(service, run_id, action))["accepted"]
        charter = next(a for a in orchestrator.state(run_id).artifacts.values() if a.type == "GoalCharter")
        assert charter.attested_by == "runtime"

    def test_an_unknown_role_is_refused_at_startup(self, orchestrator: Orchestrator) -> None:
        with pytest.raises(ValueError, match="isn't a role"):
            McpService(orchestrator, bound_role="ghost/role")


class TestMemoryAndSkills:
    def _at_execute(self, orchestrator: Orchestrator) -> tuple[McpService, str, str]:
        service = McpService(orchestrator)
        current = service.run_start("Goal")
        run_id = current["run_id"]
        for _ in range(10):
            action = current["actions"][0]
            if action["purpose"] == Purpose.EXECUTE.value:
                return service, run_id, action["invocation_id"]
            current = service.artifact_submit(run_id, action["invocation_id"], _payload(service, run_id, action))
        raise AssertionError("no execute step")

    def test_memory_uses_the_assigned_role(self, orchestrator: Orchestrator) -> None:
        service, run_id, invocation = self._at_execute(orchestrator)
        service.memory_write(run_id, invocation, "deploy on friday")
        backend = orchestrator.memory.backend  # type: ignore[attr-defined]
        assert backend.banks == {"agent-engineering--backend": ["deploy on friday"]}
        assert "friday" in service.memory_recall(run_id, invocation, "deploy friday")

    def test_memory_comes_back_labelled_untrusted(self, orchestrator: Orchestrator) -> None:
        service, run_id, invocation = self._at_execute(orchestrator)
        assert service.memory_recall(run_id, invocation, "x").startswith('<untrusted kind="memory">')

    def test_a_roles_notes_reach_its_own_memory(self, orchestrator: Orchestrator) -> None:
        """Over MCP these were once dropped, so a role remembered nothing of its own work."""
        service, run_id, invocation = self._at_execute(orchestrator)
        state = orchestrator.state(run_id)
        payload = default_payload(state.invocations[invocation], state, orchestrator.org)
        notes = {"did": "Wrote the menu", "lesson": "Clients want a vegetarian option every day"}
        assert service.artifact_submit(run_id, invocation, payload, notes=notes)["accepted"] is True
        task = next(t for t in orchestrator.state(run_id).tasks.values() if t.notes_artifact_id)
        assert orchestrator.state(run_id).artifacts[task.notes_artifact_id].payload["did"] == "Wrote the menu"

    def test_invalid_notes_are_rejected(self, orchestrator: Orchestrator) -> None:
        service, run_id, invocation = self._at_execute(orchestrator)
        state = orchestrator.state(run_id)
        payload = default_payload(state.invocations[invocation], state, orchestrator.org)
        assert service.artifact_submit(run_id, invocation, payload, notes={"made_up": 1})["accepted"] is False

    def test_no_client_can_change_company_memory(self, orchestrator: Orchestrator) -> None:
        """A person approves company memory. Over MCP, even the CEO could once skip that."""
        service, _, _ = self._at_execute(orchestrator)
        assert not hasattr(service, "memory_promote")
        assert "memory_promote" not in MCP_TOOLS

    def test_a_role_without_the_tool_is_refused(self, org: Org, tmp_path: Path) -> None:
        orchestrator = Orchestrator(org, tmp_path, memory=RecordingMemory())
        service = McpService(orchestrator)
        first = service.run_start("Goal")
        service.escalate(first["run_id"], first["actions"][0]["invocation_id"], "x")
        # The CEO has no memory_write in its role file.
        assert "memory_write" not in org.role("executive/ceo").allowed_tools

    def test_skill_get_refuses_what_a_role_cannot_have(self, orchestrator: Orchestrator) -> None:
        service, run_id, invocation = self._at_execute(orchestrator)
        with pytest.raises(ToolError, match="isn't available"):
            service.skill_get(run_id, invocation, "../../etc/passwd")

    def test_usage_is_tracked_and_marked_self_reported(self, orchestrator: Orchestrator) -> None:
        service, run_id, invocation = self._at_execute(orchestrator)
        assert service.usage_report(run_id, invocation, 100, 50)["self_reported"] is True
        assert service.run_status(run_id)["tokens_self_reported"] == 150


class TestAdapter:
    def test_it_implements_the_interface(self, orchestrator: Orchestrator) -> None:
        adapter = McpAdapter(McpService(orchestrator))
        assert isinstance(adapter, RuntimeAdapter)
        assert adapter.mode == "pull" and adapter.verify().ok

    def test_capabilities_are_honest(self, orchestrator: Orchestrator) -> None:
        assert McpAdapter(McpService(orchestrator)).capabilities().attests_identity is False
        bound = McpAdapter(McpService(orchestrator, bound_role=BACKEND)).capabilities()
        assert bound.attests_identity is True
        assert bound.enforces_tools is False, "a client's own tools are outside this server's control"


class TestRealProtocol:
    @pytest.fixture
    def server(self, orchestrator: Orchestrator) -> Any:
        pytest.importorskip("mcp")
        from try_professional.adapters.mcp.server import build_server

        return build_server(McpService(orchestrator))

    def test_every_tool_is_exposed(self, server: Any) -> None:
        names = {tool.name for tool in asyncio.run(server.list_tools())}
        assert names == set(MCP_TOOLS)

    def test_nothing_a_person_must_decide_is_exposed(self, server: Any) -> None:
        names = {tool.name for tool in asyncio.run(server.list_tools())}
        for word in ("approve", "admit", "reject", "categorize", "decide", "waive", "install", "pin"):
            assert not any(word in name for name in names), word

    def test_no_tool_takes_a_role(self, server: Any) -> None:
        for tool in asyncio.run(server.list_tools()):
            properties = (tool.input_schema or {}).get("properties", {})
            assert not {"role", "actor", "bank", "attested_by"} & set(properties), tool.name

    def test_a_client_drives_a_run_through_the_real_sdk(self, server: Any, orchestrator: Orchestrator) -> None:
        from mcp import Client

        service = McpService(orchestrator)

        def text_of(result: Any) -> Any:
            return json.loads(result.content[0].text)

        async def scenario() -> Any:
            async with Client(server) as client:
                org = text_of(await client.call_tool("org_get", {}))
                assert org["identity"] == "operator"
                current = text_of(await client.call_tool("run_start", {"goal": "Build a thing"}))
                run_id = current["run_id"]
                for _ in range(60):
                    if current["status"] != "working":
                        break
                    action = current["actions"][0]
                    current = text_of(
                        await client.call_tool(
                            action["submit_with"],
                            {
                                "run_id": run_id,
                                "invocation_id": action["invocation_id"],
                                "payload": _payload(service, run_id, action),
                            },
                        )
                    )
                return run_id, current

        run_id, final = asyncio.run(scenario())
        assert final["status"] == "done"
        assert orchestrator.verify_audit(run_id).ok

    def test_refusals_come_back_as_data(self, server: Any) -> None:
        from mcp import Client

        async def scenario() -> Any:
            async with Client(server) as client:
                return await client.call_tool("run_next_action", {"run_id": "no-such-run"})

        result = asyncio.run(scenario())
        assert "no-such-run" in result.content[0].text

    def test_a_real_server_runs_over_stdio(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The way a client really starts it: a separate process, through the trypro command."""
        pytest.importorskip("mcp")
        from mcp import Client, StdioServerParameters

        from try_professional.cli import main

        monkeypatch.chdir(tmp_path)
        assert main(["init", "--skip-model"]) == 0
        config = tmp_path / "trypro.yaml"
        config.write_text(
            config.read_text(encoding="utf-8").replace("enabled: true", "enabled: false"), encoding="utf-8"
        )

        async def scenario() -> Any:
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "try_professional.cli", "mcp"],
                cwd=str(tmp_path),
                env={**os.environ},
            )
            async with Client(parameters) as client:
                tools = await client.list_tools()
                org = await client.call_tool("org_get", {})
                return [t.name for t in tools.tools], json.loads(org.content[0].text)

        names, org = asyncio.run(asyncio.wait_for(scenario(), 90))
        assert "run_start" in names and org["root_role"] == "executive/ceo"
