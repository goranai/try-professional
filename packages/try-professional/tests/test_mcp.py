"""The MCP adapter: the service directly, the real SDK in memory, and a real server over stdio."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from try_professional.adapters.api_runner.scripted import default_payload
from try_professional.adapters.base import RuntimeAdapter
from try_professional.adapters.mcp import MCP_TOOLS, McpAdapter, McpService, ToolError, claude_code
from try_professional.core.orchestrator import Orchestrator
from try_professional.core.pipeline import HumanDecided, Purpose
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

    def test_a_role_that_cant_write_files_is_told_what_to_do_instead(self, orchestrator: Orchestrator) -> None:
        """Real reviewers and a CEO tried to write files five times in one company's runs, with no idea what next."""
        service = McpService(orchestrator)
        first = service.run_start("Goal")
        invocation = first["actions"][0]["invocation_id"]
        role = orchestrator.state(first["run_id"]).invocations[invocation].role
        if "workspace_write" in orchestrator.org.role(role).allowed_tools:
            pytest.skip("this company's first role may write files")
        with pytest.raises(ToolError, match="in your submission instead"):
            service.workspace_write(first["run_id"], invocation, "notes.md", "x")

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
        assert service.artifact_submit(run_id, invocation, payload, notes={"did": 5})["accepted"] is False

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


class TestAStoppedCommand:
    """A real command stopped at its limit, and neither way forward worked for someone on Claude Code."""

    def _stopped(self, make_org: Callable[..., Org], tmp_path: Path) -> tuple[McpService, str, dict[str, Any]]:
        def mutate(org_dir: Path) -> None:
            path = org_dir / "org.yaml"
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            # Each step here counts about 2,300 to 3,400 tokens, so half of this limit covers the next one.
            doc["pipeline"]["budget"]["mcp_max_tokens_per_run"] = 9000
            path.write_text(yaml.safe_dump(doc), encoding="utf-8")

        service = McpService(Orchestrator(make_org(mutate=mutate), tmp_path / "home"))
        run_id = (current := service.run_start("Add a health check endpoint"))["run_id"]
        for _ in range(100):
            if current["status"] == "blocked":
                return service, run_id, current
            current = self._step(service, run_id, current)
        raise AssertionError("it never stopped")

    def _step(self, service: McpService, run_id: str, current: dict[str, Any]) -> dict[str, Any]:
        action = current["actions"][0]
        answer: dict[str, Any] = getattr(service, action["submit_with"])(
            run_id, action["invocation_id"], _payload(service, run_id, action)
        )
        return answer

    def _answer(self, service: McpService, run_id: str, stopped: dict[str, Any], amount: int | None = None) -> None:
        request_id = stopped["needs_a_person"][0]["command"].split()[3]
        service.orchestrator.handle(
            run_id, HumanDecided(request_id=request_id, decision="resume", new_token_budget=amount)
        )

    def test_resuming_with_no_amount_adds_half_a_limit(self, make_org: Callable[..., Org], tmp_path: Path) -> None:
        service, run_id, stopped = self._stopped(make_org, tmp_path)
        total = service.orchestrator.state(run_id).usage.total
        self._answer(service, run_id, stopped)
        assert service.orchestrator.state(run_id).budget_override == total + 4500

    def test_the_next_step_doesnt_stop_it_again(self, make_org: Callable[..., Org], tmp_path: Path) -> None:
        service, run_id, stopped = self._stopped(make_org, tmp_path)
        self._answer(service, run_id, stopped)
        current = service.run_next_action(run_id)
        assert current["status"] == "working"
        assert self._step(service, run_id, current)["status"] != "blocked"

    def test_it_is_still_limited_after_resuming(self, make_org: Callable[..., Org], tmp_path: Path) -> None:
        """A person chose to go on, and didn't choose to have no limit."""
        service, run_id, stopped = self._stopped(make_org, tmp_path)
        self._answer(service, run_id, stopped)
        current = service.run_next_action(run_id)
        for _ in range(100):
            if current["status"] != "working":
                break
            current = self._step(service, run_id, current)
        assert current["status"] == "blocked"
        state = service.orchestrator.state(run_id)
        assert state.budget_override is not None and state.usage.total > state.budget_override

    def test_a_named_amount_still_wins(self, make_org: Callable[..., Org], tmp_path: Path) -> None:
        service, run_id, stopped = self._stopped(make_org, tmp_path)
        self._answer(service, run_id, stopped, 99_000)
        assert service.orchestrator.state(run_id).budget_override == 99_000

    def test_the_settings_keep_a_person_s_decisions_from_a_session(self, org: Org, tmp_path: Path) -> None:
        """Deny rules hold even when someone runs Claude Code with every permission on."""
        claude_code.write_claude_code_files(tmp_path, org)
        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
        assert "Bash(trypro decide *)" in settings["permissions"]["deny"]
        assert "PowerShell(trypro init --review *)" in settings["permissions"]["deny"]

    def test_a_role_without_its_tools_stops_and_says_so(self, org: Org, tmp_path: Path) -> None:
        """In a folder Claude Code hasn't trusted, it leaves out each role's own server. A real CEO with no tools
        wrote its own client for the server instead. Each role now stops with NO_ROLE_TOOLS, and the coordinator says
        what to do."""
        claude_code.write_claude_code_files(tmp_path, org)
        for agent in (tmp_path / ".claude" / "agents").glob("trypro-*.md"):
            assert "reply only NO_ROLE_TOOLS" in agent.read_text(encoding="utf-8")
        command = (tmp_path / ".claude" / "commands" / "company.md").read_text(encoding="utf-8")
        assert "NO_ROLE_TOOLS" in command and "trusted" in command and "claude -p" in command

    @pytest.mark.parametrize(("chosen", "expected"), [(None, {"sonnet"}), ("haiku", {"haiku"})])
    def test_one_model_can_be_chosen_for_every_role(
        self, org: Org, tmp_path: Path, chosen: str | None, expected: set[str]
    ) -> None:
        claude_code.write_claude_code_files(tmp_path, org, chosen)
        models = {
            yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])["model"]
            for path in (tmp_path / ".claude" / "agents").glob("trypro-*.md")
        }
        assert models == expected or (chosen is None and models <= {"sonnet", "haiku"})

    @pytest.mark.parametrize("asked", ["continue {run}", "/company continue {run}", "  Continue {run} "])
    def test_starting_continue_picks_up_that_run(
        self, make_org: Callable[..., Org], tmp_path: Path, asked: str
    ) -> None:
        """Haiku once passed "continue <run-id>" straight to run_start, which made a stray new run."""
        service, run_id, _ = self._stopped(make_org, tmp_path)
        before = len(service.orchestrator.ledger.list_runs())
        answer = service.run_start(asked.format(run=run_id))
        assert answer["run_id"] == run_id and answer["continued"]
        assert answer["status"] == "blocked"
        assert len(service.orchestrator.ledger.list_runs()) == before

    def test_continuing_a_run_that_isnt_there_is_refused(self, make_org: Callable[..., Org], tmp_path: Path) -> None:
        service, _, _ = self._stopped(make_org, tmp_path)
        with pytest.raises(ToolError):
            service.run_start("continue run-000000000000")

    def test_an_ordinary_goal_that_mentions_continuing_starts_a_run(
        self, make_org: Callable[..., Org], tmp_path: Path
    ) -> None:
        service, run_id, _ = self._stopped(make_org, tmp_path)
        assert service.run_start(f"Continue the work from {run_id}, and add a summary")["run_id"] != run_id

    def test_company_tells_the_session_how_to_continue(self) -> None:
        text = claude_code._COMMAND
        assert text.index("don't start anything") < text.index("call run_start")
        assert "needs_a_person" in text and "Never choose for me" in text

    def test_company_says_what_to_do_when_a_role_wont_start(self) -> None:
        """In a folder Claude Code doesn't trust yet, a role's own server doesn't start, and it has no tools."""
        assert "trusted" in claude_code._COMMAND
        assert "Never do a role's work yourself" in " ".join(claude_code._COMMAND.split())


class TestWhatAPersonGetsBack:
    """Each of these came from reading a real acceptance run's report."""

    def _finished(self, orchestrator: Orchestrator) -> tuple[McpService, str, dict[str, Any]]:
        service = McpService(orchestrator)
        started = service.run_start("Add a health check endpoint")
        return service, started["run_id"], _drive(service, started["run_id"], started)

    def test_it_says_where_the_deliverables_are(self, org: Org, tmp_path: Path) -> None:
        orchestrator = Orchestrator(org, tmp_path / "home", deliverables_dir=tmp_path / "out")
        _, run_id, done = self._finished(orchestrator)
        assert done["status"] == "done"
        assert done["deliverables"].endswith(f"out/{run_id}/index.md")
        assert Path(done["deliverables"]).is_file()

    def test_a_role_isnt_told_where_they_are(self, org: Org, tmp_path: Path) -> None:
        orchestrator = Orchestrator(org, tmp_path / "home", deliverables_dir=tmp_path / "out")
        _, run_id, _ = self._finished(orchestrator)
        assert McpService(orchestrator, bound_role=org.root_role).run_next_action(run_id)["deliverables"] is None

    def test_the_token_count_says_it_is_an_estimate(self, org: Org, tmp_path: Path) -> None:
        """A real run's index said 15,435 tokens, when the client's own count was over 30 times that."""
        orchestrator = Orchestrator(org, tmp_path / "home", deliverables_dir=tmp_path / "out")
        _, _, done = self._finished(orchestrator)
        index = Path(done["deliverables"]).read_text(encoding="utf-8")
        assert "estimated by the server" in index
        assert "model calls" not in index

    def test_a_final_review_is_given_the_work_it_signs_off(self, org: Org, tmp_path: Path) -> None:
        """A real legal review said it could read only the CEO's summary, and approved on that."""
        service = McpService(Orchestrator(org, tmp_path / "home", deliverables_dir=tmp_path / "out"))
        run_id = (current := service.run_start("Add a health check endpoint"))["run_id"]
        seen: list[str] = []
        for _ in range(100):
            if current["status"] != "working":
                break
            action = current["actions"][0]
            if action["task_id"] == "__final__" and action["purpose"] == "review":
                seen.append(action["assignment"])
            payload = _payload(service, run_id, action)
            if action["submit_with"] == "artifact_submit" and action["purpose"] == "execute":
                current = service.artifact_submit(
                    run_id, action["invocation_id"], payload, flags={"public_facing": True}
                )
            else:
                current = getattr(service, action["submit_with"])(run_id, action["invocation_id"], payload)
        assert current["status"] == "done"
        assert seen, "no final review happened"
        state = service.orchestrator.state(run_id)
        for task in state.tasks.values():
            if task.task_id != "__final__":
                assert f"{task.result_artifact_id} from {task.owner_role}" in seen[0]

    def test_a_person_sees_what_approving_reviewers_flagged(self, org: Org, tmp_path: Path) -> None:
        """Legal once approved and noted a missing privacy notice, and nothing passed that on."""
        service = McpService(Orchestrator(org, tmp_path / "home", deliverables_dir=tmp_path / "out"))
        run_id = (current := service.run_start("Add a health check endpoint"))["run_id"]
        for _ in range(100):
            if current["status"] != "working":
                break
            action = current["actions"][0]
            payload = _payload(service, run_id, action)
            if action["purpose"] == "review":
                payload["issues"] = [{"severity": "minor", "description": "Add a privacy notice before launch."}]
            current = getattr(service, action["submit_with"])(run_id, action["invocation_id"], payload)
        index = Path(current["deliverables"]).read_text(encoding="utf-8")
        assert "## What reviewers noted" in index
        assert "Add a privacy notice before launch." in index
