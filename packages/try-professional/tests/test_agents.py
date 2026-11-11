"""Roles in agent CLIs, mixed per role: Claude Code, Codex, Antigravity, or the API runner, in one company.

Each step is one non-interactive run of the role's agent, with only that role's trypro tools, and nothing it runs
changes something outside unasked."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from try_professional.adapters import agent_runner, codex_gate
from try_professional.adapters.agent_runner import (
    ANTIGRAVITY,
    API,
    CLAUDE_CODE,
    CODEX,
    AgentRunner,
    AgentSettings,
    RuntimePlan,
    build_step,
    codex_argv,
    role_prompt,
    step_environment,
    tokens_used,
    write_gate,
)
from try_professional.adapters.mcp import McpService, ToolError
from try_professional.adapters.mcp.claude_code import _COMMAND, ROLE_SERVER
from try_professional.org import Org

SETTINGS = AgentSettings(codex="codex", antigravity="agy", claude="claude", base_url="https://router.example/v1",
                         llm_model="gpt-6.1-sol")  # fmt: skip
ROLE = "engineering/backend"


def _value(argv: list[str], key: str) -> str:
    return next(a.split("=", 1)[1] for a in argv if a.startswith(key + "="))


class TestPlan:
    def test_a_role_then_its_department_then_the_default(self) -> None:
        plan = RuntimePlan.from_config(
            {"runtime": {"default": "claude-code", "roles": {"research": "antigravity", ROLE: "codex"}},
             "models": {"antigravity": "gemini-3.8-flash-high"}}, {})  # fmt: skip
        assert plan.for_role(ROLE) == CODEX
        assert plan.for_role("research/bug-hunter") == ANTIGRAVITY
        assert plan.for_role("qa/reviewer") == CLAUDE_CODE
        assert plan.models[ANTIGRAVITY] == "gemini-3.8-flash-high"

    def test_one_name_or_the_env_sets_the_default(self) -> None:
        assert RuntimePlan.from_config({}, {}).default == API
        assert RuntimePlan.from_config({"runtime": "Codex"}, {}).default == CODEX
        assert RuntimePlan.from_config({"runtime": "codex"}, {"TRYPRO_RUNTIME": "antigravity"}).default == ANTIGRAVITY

    def test_an_unknown_runtime_says_which_exist(self) -> None:
        with pytest.raises(ValueError, match="Use one of"):
            RuntimePlan.from_config({"runtime": {"roles": {"qa": "cursor"}}}, {})


class TestSteps:
    def test_codex_gets_one_role_its_provider_and_model(self, tmp_path: Path) -> None:
        argv = codex_argv(SETTINGS, tmp_path, ROLE, "gpt-6.1-sol")
        assert argv[:2] == ["codex", "exec"] and argv[-1] == "-", "the prompt comes from stdin"
        args = json.loads(_value(argv, f"mcp_servers.{ROLE_SERVER}.args"))
        assert args[args.index("--role") + 1] == ROLE and "--coordinator" not in args
        assert json.loads(_value(argv, "model_providers.trypro.base_url")) == "https://router.example/v1"
        assert json.loads(_value(argv, "model_providers.trypro.wire_api")) == "responses"
        assert "TRYPRO_LLM_API_KEY" in json.loads(_value(argv, "shell_environment_policy.exclude"))
        assert argv[argv.index("-m") + 1] == "gpt-6.1-sol"

    def test_codex_without_an_endpoint_keeps_its_own_settings(self, tmp_path: Path) -> None:
        argv = codex_argv(AgentSettings(codex="codex"), tmp_path, ROLE, None)
        assert not any(a.startswith("model_provider") for a in argv) and "-m" not in argv

    def test_antigravity_starts_in_a_folder_with_only_this_roles_server(self, tmp_path: Path) -> None:
        step = build_step(ANTIGRAVITY, SETTINGS, tmp_path, ROLE, "gemini-3.8-flash-high", "do it",
                          tmp_path / "work", tmp_path / "step")  # fmt: skip
        config = json.loads((tmp_path / "step" / ".agents" / "mcp_config.json").read_text(encoding="utf-8"))
        assert list(config["mcpServers"]) == [ROLE_SERVER] and ROLE in config["mcpServers"][ROLE_SERVER]["args"]
        assert step.cwd == tmp_path / "step" and step.argv[:3] == ["agy", "-p", "do it"]
        assert step.argv[step.argv.index("--model") + 1] == "gemini-3.8-flash-high"
        assert step.argv[step.argv.index("--add-dir") + 1] == str(tmp_path / "work")
        assert "--dangerously-skip-permissions" in step.argv, "a role runs commands, as a person set it"
        off = AgentSettings(antigravity="agy", antigravity_bypass=False)
        assert "--dangerously-skip-permissions" not in build_step(
            ANTIGRAVITY, off, tmp_path, ROLE, None, "x", tmp_path, tmp_path / "s2").argv  # fmt: skip

    def test_claude_code_gets_only_this_roles_server(self, tmp_path: Path) -> None:
        step = build_step(CLAUDE_CODE, SETTINGS, tmp_path, ROLE, "sonnet", "do it", tmp_path, tmp_path / "s")
        config = json.loads(step.argv[step.argv.index("--mcp-config") + 1])
        assert list(config["mcpServers"]) == [ROLE_SERVER] and "--strict-mcp-config" in step.argv
        assert step.argv[step.argv.index("--allowedTools") + 1].startswith(f"mcp__{ROLE_SERVER}__*")
        assert step.stdin == "do it"

    def test_tokens_come_from_what_each_agent_prints(self) -> None:
        assert tokens_used(CODEX, "codex\nok\ntokens used\n11,080\n") == 11080
        assert tokens_used(ANTIGRAVITY, '{"status":"SUCCESS","usage":{"total_tokens":13263}}') == 13263
        assert tokens_used(CLAUDE_CODE, '{"usage":{"input_tokens":100,"output_tokens":20}}') == 120
        assert tokens_used(ANTIGRAVITY, "no json here") is None

    def test_secrets_stay_out_but_a_github_token_and_codexs_own_key(self, tmp_path: Path) -> None:
        environ = {"PATH": "/bin", "TRYPRO_LLM_API_KEY": "k", "GITHUB_TOKEN": "g", "STRIPE_SECRET": "s",
                   "AWS_SECRET_ACCESS_KEY": "a", "HOME": "/home/x"}  # fmt: skip
        codex = step_environment(environ, "TRYPRO_LLM_API_KEY", tmp_path)
        assert codex["TRYPRO_LLM_API_KEY"] == "k" and codex["GITHUB_TOKEN"] == "g" and codex["HOME"] == "/home/x"
        assert "STRIPE_SECRET" not in codex and "AWS_SECRET_ACCESS_KEY" not in codex
        assert codex["PATH"].startswith(str(tmp_path)), "gh and git are the gate's"
        other = step_environment(environ, None, tmp_path)
        assert "TRYPRO_LLM_API_KEY" not in other, "an agent that doesn't need the key never gets it"

    def test_only_codex_with_the_companys_endpoint_gets_its_key(self, org: Org, harness: Callable[..., Any]) -> None:
        run, envs = harness(org), []

        def capture(argv: list[str], stdin: str, env: dict[str, str], cwd: Path, timeout: int) -> tuple[int, str]:
            envs.append(env)
            assert not str(cwd).startswith(str(run.orchestrator.home)), "a step starts outside the company"
            return 0, ""

        environ = {"PATH": "", "TRYPRO_LLM_API_KEY": "k"}
        for runtime in (CODEX, ANTIGRAVITY, CLAUDE_CODE):
            plan = RuntimePlan(default=runtime, models={ANTIGRAVITY: "gemini-3.8-flash-high"})
            runner = AgentRunner(run.orchestrator, plan, SETTINGS, root=run.orchestrator.home, run_process=capture,
                                 environ=environ)  # fmt: skip
            run_id, _ = run.orchestrator.start("Goal")
            action = next(a for a in run.orchestrator.open_actions(run_id) if a.kind == "invoke_role")
            runner._invoke(action, run_id)
            assert ("TRYPRO_LLM_API_KEY" in envs[-1]) == (runtime == CODEX), runtime

    def test_a_companys_own_key_name_is_used(self, tmp_path: Path) -> None:
        from dataclasses import replace

        found = AgentSettings.from_environment({"TRYPRO_LLM_BASE_URL": "https://x/v1"}, key_env="DEEPSEEK_API_KEY")
        settings = replace(found, codex="codex")  # whether Codex is installed here doesn't matter
        argv = codex_argv(settings, tmp_path, ROLE, None)
        assert json.loads(_value(argv, "model_providers.trypro.env_key")) == "DEEPSEEK_API_KEY"
        env = step_environment({"DEEPSEEK_API_KEY": "k", "PATH": ""}, settings.key_env, tmp_path)
        assert env["DEEPSEEK_API_KEY"] == "k"

    def test_the_prompt_names_the_run_its_folder_and_what_waits(self, org: Org, tmp_path: Path) -> None:
        text = role_prompt(org, org.role(ROLE), "run-1", tmp_path)
        assert text.rstrip().endswith("run_id: run-1") and str(tmp_path) in text
        assert "waits for a person" in text and ROLE_SERVER in text

    def test_every_agent_starts_in_its_own_folder_with_the_commands_folder_added(self, tmp_path: Path) -> None:
        for runtime in (CODEX, ANTIGRAVITY, CLAUDE_CODE):
            step = build_step(runtime, SETTINGS, tmp_path, ROLE, None, "x", tmp_path / "work", tmp_path / runtime)
            assert step.cwd == tmp_path / runtime
            assert step.argv[step.argv.index("--add-dir") + 1] == str(tmp_path / "work")


class TestGate:
    def test_wrappers_stand_in_front_of_the_real_programs(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        for name in ("gh", "git"):
            program = real / (f"{name}.exe" if sys.platform == "win32" else name)
            program.write_text("", encoding="utf-8")
            program.chmod(0o755)
        gate = write_gate(tmp_path / "gate", tmp_path, "run-1", ROLE, tmp_path, str(real))
        script = (gate / "gh").read_text(encoding="utf-8")
        assert "try_professional.adapters.codex_gate" in script and str(real) in script and "run-1" in script
        assert (gate / "git").is_file()

    def test_a_held_command_runs_later_from_where_it_was_typed(self, tmp_path: Path) -> None:
        (tmp_path / "repo").mkdir()
        assert codex_gate.held_command("gh", ["pr", "create", "--fill"], tmp_path / "repo", tmp_path) == (
            "cd repo && gh pr create --fill"
        )
        assert codex_gate.held_command("git", ["push"], tmp_path, tmp_path) == "git push"
        # Typed in the step's own folder, which is gone by then, it runs from the workspace; elsewhere, from there.
        step = tmp_path / "trypro-step-inv-1-abc" / "start"
        assert codex_gate.held_command("git", ["push"], step, tmp_path / "x") == "git push"
        assert codex_gate.held_command("git", ["push"], tmp_path / "repo", tmp_path / "x").startswith("cd ")

    @pytest.mark.parametrize(
        "args",
        [
            # Each of these ran without asking in the review of 0.7.3's first gate.
            "gh pr -R o/r create --fill",
            "gh issue --repo o/r comment 1 -b x",
            "gh api repos/o/r/issues -f title=x",
            "gh api graphql -f query=mutation{x}",
            "gh api --input body.json repos/o/r/pulls",
            "gh workflow run ci",
            "gh release upload v1 a.zip",
            "gh secret set X",
            "gh repo rename y",
            "gh pr ready 1",
            "gh label create bug",
            "gh issue transfer 1 o/s",
            "gh run rerun 1",
            "gh alias set x y",
            "gh auth token",
            "git lfs push origin main",
            "git svn dcommit",
            "git -c alias.p=push p",
            "git push",
            "git -C repo push origin main",
            "git send-email x.patch",
            "git my-alias",
            # And these, from its second review: options gh reads attached or bundled, and settings beyond this copy.
            "gh api -ftitle=x repos/o/r/issues",
            "gh api -Ftitle=x repos/o/r/issues",
            "gh api -if title=x repos/o/r",
            "gh api -iXPOST repos/o/r/issues",
            "gh api graphql -F query=@m.graphql",
            "gh api graphql --input m.json",
            "gh api graphql -f query=@m.graphql",
            "gh auth status -t",
            "gh auth status --show-token",
            "git config --global alias.p push",
            "git config --file /x/config k v",
            "git lfs install",
            # And these, from its third review: git's abbreviations, the OS scheduler, and a shown token.
            "git config --glo alias.p push",
            "git config --gl k v",
            "git config --fil=/x k v",
            "git config -f/x/config k v",
            "git config --sys k v",
            "git maintenance register",
            "git maintenance start",
            "gh auth status --show-token=true",
        ],
    )
    def test_anything_that_may_change_something_outside_waits(self, args: str) -> None:
        name, *rest = args.split()
        assert codex_gate.changes_something(name, rest), args

    @pytest.mark.parametrize(
        "args",
        [
            "gh pr list",
            "gh pr view 1 --json title",
            "gh -R o/r issue list",
            "gh api repos/o/r",
            "gh api -X GET search/issues -f q=x",
            "gh api graphql -f query={viewer{login}}",
            "gh search issues bug",
            "gh repo clone o/r",
            "gh auth status",
            "gh --version",
            "git status",
            "git log --oneline",
            "git -C repo diff",
            "git fetch origin",
            "git clone https://github.com/o/r",
            "git commit -m x",
            "git checkout -b fix",
            "git lfs pull",
            "git --version",
            "gh api -i repos/o/r",
            "gh api -XGET search/x",
            "git config user.name x",
            "git config --get remote.origin.url",
            "git config --list",
            "git maintenance run",
        ],
    )
    def test_reading_and_local_work_run(self, args: str) -> None:
        name, *rest = args.split()
        assert not codex_gate.changes_something(name, rest), args

    def test_reading_runs_and_a_change_outside_is_held(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ran: list[list[str]] = []
        held: list[tuple[str, str, str]] = []
        monkeypatch.setattr(codex_gate.subprocess, "call", lambda argv: ran.append(argv) or 0)

        class Outside:
            def hold_command(self, run_id: str, role: str, command: str) -> int:
                held.append((run_id, role, command))
                return 7

            def close(self) -> None:
                pass

        import try_professional.cli as cli

        monkeypatch.setattr(cli, "_outside_orchestrator", lambda root, with_memory: Outside())
        monkeypatch.chdir(tmp_path)
        base = [str(tmp_path), "run-1", ROLE, str(tmp_path)]
        assert codex_gate.main([*base, "/usr/bin/gh", "pr", "list"]) == 0
        assert ran == [["/usr/bin/gh", "pr", "list"]]
        assert codex_gate.main([*base, "/usr/bin/gh", "pr", "create", "--fill"]) == 3
        assert codex_gate.main([*base, "/usr/bin/git", "-C", ".", "push"]) == 3
        assert held == [("run-1", ROLE, "gh pr create --fill"), ("run-1", ROLE, "git -C . push")]
        assert len(ran) == 1, "nothing that changes something outside ran"


def _role_of(argv: list[str], cwd: Path) -> str:
    """Which role's server an agent was given, however that agent takes it."""
    if "--mcp-config" in argv:
        args = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"][ROLE_SERVER]["args"]
    elif (cwd / ".agents" / "mcp_config.json").is_file():
        config = json.loads((cwd / ".agents" / "mcp_config.json").read_text(encoding="utf-8"))
        args = config["mcpServers"][ROLE_SERVER]["args"]
    else:
        args = json.loads(_value(argv, f"mcp_servers.{ROLE_SERVER}.args"))
    return str(args[args.index("--role") + 1])


def _fake_agents(run: Any, seen: list[str], *, hand_in: bool = True, say: str = "") -> Callable[..., tuple[int, str]]:
    """Stands in for every agent CLI: the role whose server it was given does its step through the company's tools,
    as the agent would. A scripted model does the work, in process."""

    def run_process(argv: list[str], stdin: str, env: dict[str, str], cwd: Path, timeout: int) -> tuple[int, str]:
        role = _role_of(argv, cwd)
        seen.append(f"{Path(argv[0]).stem}:{role}")
        prompt = stdin or argv[argv.index("-p") + 1]
        run_id = prompt.rsplit("run_id: ", 1)[1].strip()
        if hand_in:
            action = next(a for a in run.orchestrator.open_actions(run_id)
                          if getattr(a, "role", None) == role and a.kind == "invoke_role")  # fmt: skip
            for event in run.runner._invoke(action, run_id):
                run.orchestrator.handle(run_id, event)
        return 0, f"{say}\ntokens used\n11,080\n"

    return run_process


class TestRunner:
    def test_one_command_with_roles_in_different_agents(self, org: Org, harness: Callable[..., Any]) -> None:
        run, seen = harness(org), []
        plan = RuntimePlan(default=CODEX, roles={"engineering": ANTIGRAVITY})
        runner = AgentRunner(run.orchestrator, plan, SETTINGS, root=run.orchestrator.home,
                             run_process=_fake_agents(run, seen), environ={"PATH": ""})  # fmt: skip
        result = runner.run("Goal")
        assert result.status == "done"
        assert any(s.startswith("agy:engineering/") for s in seen), seen
        assert any(s.startswith("codex:") and ":engineering/" not in s for s in seen), seen
        audit = run.orchestrator.audit_path(result.run_id).read_text(encoding="utf-8")
        assert '"agent_step"' in audit and '"runtime": "antigravity"' in audit and '"runtime": "codex"' in audit
        assert not (run.orchestrator.home / "steps").exists(), "steps leave nothing in the company's folder"

    def test_roles_on_the_api_runner_go_to_it(self, org: Org, harness: Callable[..., Any]) -> None:
        run, seen = harness(org), []
        plan = RuntimePlan(default=API, roles={"engineering": ANTIGRAVITY})
        runner = AgentRunner(run.orchestrator, plan, SETTINGS, root=run.orchestrator.home, api=run.runner,
                             run_process=_fake_agents(run, seen), environ={"PATH": ""})  # fmt: skip
        assert runner.run("Goal").status == "done"
        assert seen and all(s.startswith("agy:engineering/") for s in seen), seen

    def test_a_step_that_hands_nothing_in_fails_and_is_retried(self, org: Org, harness: Callable[..., Any]) -> None:
        run = harness(org)
        runner = AgentRunner(run.orchestrator, RuntimePlan(default=CODEX), SETTINGS, root=run.orchestrator.home,
                             run_process=_fake_agents(run, [], hand_in=False), environ={"PATH": ""})  # fmt: skip
        result = runner.run("Goal")
        assert result.status in ("blocked", "failed")
        assert "without handing in" in run.orchestrator.audit_path(result.run_id).read_text(encoding="utf-8")

    def test_missing_role_tools_say_so(self, org: Org, harness: Callable[..., Any]) -> None:
        run = harness(org)
        runner = AgentRunner(run.orchestrator, RuntimePlan(default=CODEX), SETTINGS, root=run.orchestrator.home,
                             run_process=_fake_agents(run, [], hand_in=False, say="NO_ROLE_TOOLS"),
                             environ={"PATH": ""})  # fmt: skip
        result = runner.run("Goal")
        assert "didn't load the role's trypro tools" in run.orchestrator.audit_path(result.run_id).read_text(
            encoding="utf-8"
        )

    def test_a_role_on_the_api_runner_with_no_endpoint_says_so(self, org: Org, harness: Callable[..., Any]) -> None:
        run = harness(org)
        runner = AgentRunner(run.orchestrator, RuntimePlan(default=API), SETTINGS, root=run.orchestrator.home,
                             run_process=_fake_agents(run, []), environ={"PATH": ""})  # fmt: skip
        result = runner.run("Goal")
        assert "no model endpoint is set up" in run.orchestrator.audit_path(result.run_id).read_text(encoding="utf-8")


class TestFromClaudeCode:
    """Coordinating in Claude Code, a role set to another agent is handed to it with run_step."""

    def test_the_command_says_how(self) -> None:
        assert "runs_in" in _COMMAND and "run_step" in _COMMAND

    def test_actions_say_where_a_role_works_and_run_step_does_it(
        self, org: Org, harness: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run, seen = harness(org), []
        fake = _fake_agents(run, seen)

        def step(orchestrator: Any, action: Any, run_id: str, runtime: str, **kwargs: Any) -> list[Any]:
            kwargs.update(run_process=fake, environ={"PATH": ""})
            return original(orchestrator, action, run_id, runtime, **kwargs)

        original = agent_runner.run_agent_step
        monkeypatch.setattr(agent_runner, "run_agent_step", step)
        plan = RuntimePlan(default=CLAUDE_CODE, roles={"engineering": ANTIGRAVITY})
        coordinator = McpService(run.orchestrator, coordinator=True, runtimes=plan, agents=SETTINGS,
                                 root=run.orchestrator.home)  # fmt: skip
        answer = coordinator.run_start("Goal")
        run_id = answer["run_id"]
        for _ in range(40):
            if answer["status"] != "working":
                break
            for action in answer["actions"]:
                if action.get("runs_in"):
                    assert action["role"].startswith("engineering/") and action["runs_in"] == ANTIGRAVITY
                    assert coordinator.run_step(run_id, action["invocation_id"])["status"] == "handed_in"
                else:
                    assert not action["role"].startswith("engineering/")
                    with pytest.raises(ToolError, match="subagent"):
                        coordinator.run_step(run_id, action["invocation_id"])
                    invoke = next(a for a in run.orchestrator.open_actions(run_id)
                                  if getattr(a, "invocation_id", None) == action["invocation_id"])  # fmt: skip
                    for event in run.runner._invoke(invoke, run_id):
                        run.orchestrator.handle(run_id, event)
            answer = coordinator.run_next_action(run_id)
        assert answer["status"] == "done" and any(s.startswith("agy:engineering/") for s in seen)

    def test_only_the_coordinator_hands_a_step_on(self, org: Org, harness: Callable[..., Any]) -> None:
        run = harness(org)
        with pytest.raises(ToolError, match="Only the coordinator"):
            McpService(run.orchestrator).run_step("run-1", "inv-1")


def test_the_coordinator_refuses_an_unknown_runtime_before_opening_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo in runtime is said on every call, rather than quietly making a role a subagent, and nothing is opened."""
    import argparse

    import try_professional.adapters.mcp.server as server
    import try_professional.cli as cli
    from try_professional import Company

    root = tmp_path / "acme"
    Company.create(root, name="Acme")
    with (root / "trypro.yaml").open("a", encoding="utf-8") as config:
        config.write("\nruntime:\n  roles:\n    engineering: cursor\n")
    loaders: list[Any] = []

    class Built:
        def run(self, transport: str) -> None:
            pass

    monkeypatch.setattr(server, "build_server", lambda company, design, coordinator: loaders.append(company) or Built())
    monkeypatch.setattr(cli, "use_embedding_service", lambda home: None)
    monkeypatch.setattr(cli, "Orchestrator", lambda *a, **k: pytest.fail("opened a company before refusing"))
    args = argparse.Namespace(dir=str(root), role=None, coordinator=True, sandboxed=False)
    assert cli.cmd_mcp(args) == 0
    with pytest.raises(ToolError, match="Unknown runtime"):
        loaders[0]()
