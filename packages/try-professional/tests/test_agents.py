"""Roles in agent CLIs, mixed per role: Claude Code, Codex, Antigravity, or the API runner, in one company.

Each step is one non-interactive run of the role's agent, with only that role's trypro tools, and nothing it runs
changes something outside unasked."""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from try_professional.adapters import agent_runner, codex_gate
from try_professional.adapters.agent_runner import (
    ANTIGRAVITY,
    API,
    CLAUDE_CODE,
    CODEX,
    NO_TOOLS_MAX_BACKOFF_SECONDS,
    NO_TOOLS_WAIT_SECONDS,
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


def _with_codex(environ: dict[str, str]) -> AgentSettings:
    """Settings read from environ, with Codex found: CI machines don't have it installed."""
    return dataclasses.replace(AgentSettings.from_environment(environ), codex="codex")


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
        assert 'mcp_servers.trypro_role.default_tools_approval_mode="approve"' in argv, "codex exec has nobody to ask"

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

    def test_totals_without_a_verified_usage_split_remain_unavailable(self) -> None:
        assert tokens_used(CODEX, "codex\nok\ntokens used\n11,080\n") is None
        assert tokens_used(ANTIGRAVITY, '{"status":"SUCCESS","usage":{"total_tokens":13263}}') is None
        assert tokens_used(CLAUDE_CODE, '{"usage":{"input_tokens":100,"output_tokens":20}}') is None
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

    @pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
    def test_a_projects_pythonpath_doesnt_break_it(self, tmp_path: Path) -> None:
        """A repository's tests set PYTHONPATH to its own source, and call git. When that source had a package of the
        same name, the gate couldn't start, git failed, and the role went to read the gate's code."""
        import subprocess

        shadow = tmp_path / "src" / "try_professional"
        shadow.mkdir(parents=True)
        (shadow / "__init__.py").write_text("", encoding="utf-8")
        gate = write_gate(tmp_path / "gate", tmp_path, "run-1", ROLE, tmp_path, os.environ.get("PATH", ""))
        wrapper = gate / ("git.cmd" if sys.platform == "win32" else "git")
        env = {**os.environ, "PYTHONPATH": str(tmp_path / "src")}
        done = subprocess.run([str(wrapper), "--version"], capture_output=True, text=True, env=env, timeout=120)
        assert done.returncode == 0 and "git version" in done.stdout, done.stderr

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
                             environ={"PATH": ""}, sleep=lambda s: None)  # fmt: skip
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

    monkeypatch.setattr(server, "build_server", lambda company, design, **k: loaders.append(company) or Built())
    monkeypatch.setattr(cli, "use_embedding_service", lambda home: None)
    monkeypatch.setattr(cli, "Orchestrator", lambda *a, **k: pytest.fail("opened a company before refusing"))
    args = argparse.Namespace(dir=str(root), role=None, coordinator=True, sandboxed=False, client="claude-code")
    assert cli.cmd_mcp(args) == 0
    with pytest.raises(ToolError, match="Unknown runtime"):
        loaders[0]()


class TestDeskSteps:
    """A charter, a plan or an assignment works from its brief: a real company's first three steps each explored the
    repository from scratch, and together cost more than one agent fixing the whole bug."""

    def test_every_agent_gives_a_desk_step_no_shell_or_files(self, tmp_path: Path) -> None:
        settings = AgentSettings(codex="codex", antigravity="agy", claude="claude", antigravity_agent="work",
                                 antigravity_desk_agent="desk")  # fmt: skip
        claude = build_step(CLAUDE_CODE, settings, tmp_path, ROLE, None, "x", tmp_path, tmp_path / "c", desk=True)
        assert claude.argv[claude.argv.index("--allowedTools") + 1] == f"mcp__{ROLE_SERVER}__*"
        codex = build_step(CODEX, settings, tmp_path, ROLE, None, "x", tmp_path, tmp_path / "x", desk=True)
        assert codex.argv[codex.argv.index("--sandbox") + 1] == "read-only"
        agy = build_step(ANTIGRAVITY, settings, tmp_path, ROLE, None, "x", tmp_path, tmp_path / "a", desk=True)
        assert agy.argv[agy.argv.index("--agent") + 1] == "desk"
        work = build_step(ANTIGRAVITY, settings, tmp_path, ROLE, None, "x", tmp_path, tmp_path / "w")
        assert work.argv[work.argv.index("--agent") + 1] == "work"

    def test_the_prompt_says_so(self, org: Org) -> None:
        assert "don't explore" in role_prompt(org, org.role(ROLE), "run-1", desk=True)
        assert "don't explore" not in role_prompt(org, org.role(ROLE), "run-1")

    def test_only_work_review_and_integration_get_the_computer(self, org: Org, harness: Callable[..., Any]) -> None:
        run, desks = harness(org), []

        def capture(argv: list[str], stdin: str, env: dict[str, str], cwd: Path, timeout: int) -> tuple[int, str]:
            desks.append("don't explore" in stdin)
            return 0, ""

        runner = AgentRunner(run.orchestrator, RuntimePlan(default=CLAUDE_CODE), SETTINGS,
                             root=run.orchestrator.home, run_process=capture, environ={"PATH": ""})  # fmt: skip
        run_id, _ = run.orchestrator.start("Goal")
        intake = next(a for a in run.orchestrator.open_actions(run_id) if a.kind == "invoke_role")
        runner._invoke(intake, run_id)
        assert desks == [True], "a charter is a desk step"


class TestPlanLimits:
    """On Gemini's plan a company's steps hit a few-minute limit and failed one after another, until the run stopped
    for a person. A step stopped by a limit now waits until it lifts, and runs again."""

    def test_the_wait_comes_from_what_the_agent_said(self) -> None:
        from try_professional.adapters.agent_runner import DEFAULT_LIMIT_WAIT_SECONDS, limit_wait

        said = 'AGY_ERROR: {"short_error":"RESOURCE_EXHAUSTED (code 429): Individual quota reached. Resets in 3m45s."}'
        assert limit_wait(said) == 225 + 15
        assert limit_wait("HTTP 429 Too Many Requests") == DEFAULT_LIMIT_WAIT_SECONDS
        assert limit_wait("all done") is None
        busy = "ERROR: stream disconnected before completion: The system is currently experiencing high demand"
        assert limit_wait(busy) == DEFAULT_LIMIT_WAIT_SECONDS, "a provider's busy hour is waited out"

    def test_a_limited_step_waits_and_runs_again(self, org: Org, harness: Callable[..., Any]) -> None:
        run, seen, waits = harness(org), [], []
        hands_in = _fake_agents(run, seen)
        calls = {"n": 0}

        def limited_once(argv: list[str], stdin: str, env: dict[str, str], cwd: Path, timeout: int) -> tuple[int, str]:
            calls["n"] += 1
            if calls["n"] == 1:
                return 3, "RESOURCE_EXHAUSTED (code 429): Individual quota reached. Resets in 10s."
            return hands_in(argv, stdin, env, cwd, timeout)

        runner = AgentRunner(run.orchestrator, RuntimePlan(default=CODEX), SETTINGS, root=run.orchestrator.home,
                             run_process=limited_once, environ={"PATH": ""}, sleep=waits.append)  # fmt: skip
        result = runner.run("Goal")
        assert result.status == "done" and waits == [25.0]
        audit = run.orchestrator.audit_path(result.run_id).read_text(encoding="utf-8")
        assert '"limit_waited_seconds": 25.0' in audit and "invocation_failed" not in audit

    def test_a_limit_days_away_stops_the_step_and_says_so(self, org: Org, harness: Callable[..., Any]) -> None:
        """A lab step slept toward a weekly limit 167 hours away, for two and a half hours, until it was found."""
        run, waits = harness(org), []

        def weekly(argv: list[str], stdin: str, env: dict[str, str], cwd: Path, timeout: int) -> tuple[int, str]:
            return 3, "RESOURCE_EXHAUSTED (code 429): Individual quota reached. Resets in 167h35m40s."

        runner = AgentRunner(run.orchestrator, RuntimePlan(default=ANTIGRAVITY), SETTINGS, root=run.orchestrator.home,
                             run_process=weekly, environ={"PATH": ""}, sleep=waits.append)  # fmt: skip
        run_id, _ = run.orchestrator.start("Goal")
        action = next(a for a in run.orchestrator.open_actions(run_id) if a.kind == "invoke_role")
        events = runner._invoke(action, run_id)
        assert waits == [] and not events[0].retryable
        assert "lifts in about 168 hours" in events[0].reason and "another account" in events[0].reason

    def test_a_step_cut_short_by_the_agents_service_carries_on_its_conversation(
        self, org: Org, harness: Callable[..., Any]
    ) -> None:
        """A hunter lost 241k tokens of work when Google's server sent GOAWAY, and started over from nothing."""
        from try_professional.adapters.agent_runner import CONTINUE_PROMPT

        run, seen, argvs = harness(org), [], []
        hands_in = _fake_agents(run, seen)

        def cut_once(argv: list[str], stdin: str, env: dict[str, str], cwd: Path, timeout: int) -> tuple[int, str]:
            argvs.append(argv)
            if len(argvs) == 1:
                return 3, ('{"conversation_id":"52fcd179-0dee-42b6-b842-24a2504d14bf","status":"ERROR"}\n'
                           'AGY_ERROR: {"short_error":"http2: server sent GOAWAY","status":"UNKNOWN"}')  # fmt: skip
            # The real agent carries its first prompt in the conversation; this stand-in reads it again.
            return hands_in(argvs[0], stdin, env, cwd, timeout)

        runner = AgentRunner(run.orchestrator, RuntimePlan(default=ANTIGRAVITY), SETTINGS, root=run.orchestrator.home,
                             run_process=cut_once, environ={"PATH": ""}, sleep=lambda s: None)  # fmt: skip
        run_id, _ = run.orchestrator.start("Goal")
        action = next(a for a in run.orchestrator.open_actions(run_id) if a.kind == "invoke_role")
        assert runner._invoke(action, run_id) == []
        again = argvs[1]
        assert again[again.index("--conversation") + 1] == "52fcd179-0dee-42b6-b842-24a2504d14bf"
        assert again[again.index("-p") + 1] == CONTINUE_PROMPT

    def test_a_step_that_just_stops_isnt_carried_on(self, org: Org, harness: Callable[..., Any]) -> None:
        run, argvs = harness(org), []

        def stops(argv: list[str], stdin: str, env: dict[str, str], cwd: Path, timeout: int) -> tuple[int, str]:
            argvs.append(argv)
            return 0, '{"conversation_id":"52fcd179-0dee-42b6-b842-24a2504d14bf","status":"SUCCESS"}'

        runner = AgentRunner(run.orchestrator, RuntimePlan(default=ANTIGRAVITY), SETTINGS, root=run.orchestrator.home,
                             run_process=stops, environ={"PATH": ""}, sleep=lambda s: None)  # fmt: skip
        run_id, _ = run.orchestrator.start("Goal")
        action = next(a for a in run.orchestrator.open_actions(run_id) if a.kind == "invoke_role")
        assert runner._invoke(action, run_id)[0].reason.startswith("antigravity ended")
        assert len(argvs) == 1


class TestGateOff:
    """Where nothing can change outside anyway, such as a container with no way out, a person can turn the gate off:
    a role that found git wrapped went to read the gate's code, and the arms of a study weren't alike."""

    def test_off_means_no_wrappers_and_no_note(self, org: Org, harness: Callable[..., Any]) -> None:
        run, envs, prompts = harness(org), [], []

        def capture(argv: list[str], stdin: str, env: dict[str, str], cwd: Path, timeout: int) -> tuple[int, str]:
            envs.append(env)
            prompts.append(stdin)
            return 0, ""

        from dataclasses import replace

        settings = replace(SETTINGS, gate=False)
        runner = AgentRunner(run.orchestrator, RuntimePlan(default=CLAUDE_CODE), settings, root=run.orchestrator.home,
                             run_process=capture, environ={"PATH": "/usr/bin"})  # fmt: skip
        run_id, _ = run.orchestrator.start("Goal")
        action = next(a for a in run.orchestrator.open_actions(run_id) if a.kind == "invoke_role")
        runner._invoke(action, run_id)
        assert envs[0]["PATH"] == "/usr/bin" and "waits for a person" not in prompts[0]

    def test_on_by_default_and_off_only_when_asked(self) -> None:
        assert AgentSettings.from_environment({}).gate
        assert not AgentSettings.from_environment({"TRYPRO_AGENT_GATE": "0"}).gate


class TestFromAntigravity:
    """Antigravity as the front desk: the folder's rules make it one, and every role's step runs in its own `agy -p`.
    Antigravity with its permissions skipped asks the person nothing, so it records no decision of theirs."""

    def test_the_folder_becomes_a_front_desk(self, tmp_path: Path) -> None:
        from try_professional import Company
        from try_professional.adapters.mcp.antigravity import write_antigravity_files

        root = tmp_path / "acme"
        company = Company.create(root, name="Acme")
        (root / ".agents").mkdir()
        (root / ".agents" / "mcp_config.json").write_text('{"mcpServers": {"mine": {"command": "x"}}}', "utf-8")
        write_antigravity_files(root, company.org)
        rule = (root / ".agents" / "rules" / "trypro-front-desk.md").read_text(encoding="utf-8")
        assert yaml.safe_load(rule.split("---")[1])["trigger"] == "always_on" and "Acme" in rule and "run_step" in rule
        assert "trypro decide" in rule, "a run's question is the person's to answer, at a terminal"
        servers = json.loads((root / ".agents" / "mcp_config.json").read_text(encoding="utf-8"))["mcpServers"]
        assert servers["mine"] == {"command": "x"}, "the person's own servers are kept"
        args = servers["trypro"]["args"]
        assert "--coordinator" in args and args[args.index("--client") + 1] == "antigravity"
        assert "type" not in servers["trypro"]
        assert (root / ".agents" / "workflows" / "company.md").is_file()

    def test_its_coordinator_has_no_tool_that_records_a_persons_decision(self) -> None:
        import asyncio

        from try_professional.adapters.mcp.server import build_server
        from try_professional.adapters.mcp.service import PERSON_TOOLS

        def names(**kwargs: Any) -> set[str]:
            tools = asyncio.run(build_server(lambda: None, None, coordinator=True, **kwargs).list_tools())
            return {tool.name for tool in tools}

        assert set(PERSON_TOOLS) <= names()
        assert not set(PERSON_TOOLS) & names(person_tools=False) and "run_step" in names(person_tools=False)

    def test_every_role_runs_in_its_own_agent(
        self, org: Org, harness: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run, seen = harness(org), []
        fake = _fake_agents(run, seen)

        def step(orchestrator: Any, action: Any, run_id: str, runtime: str, **kwargs: Any) -> list[Any]:
            kwargs.update(run_process=fake, environ={"PATH": ""})
            return original(orchestrator, action, run_id, runtime, **kwargs)

        original = agent_runner.run_agent_step
        monkeypatch.setattr(agent_runner, "run_agent_step", step)
        plan = RuntimePlan(default=ANTIGRAVITY, roles={"engineering": CLAUDE_CODE})
        coordinator = McpService(run.orchestrator, coordinator=True, runtimes=plan, agents=SETTINGS,
                                 root=run.orchestrator.home, client=ANTIGRAVITY)  # fmt: skip
        answer = coordinator.run_start("Goal")
        run_id = answer["run_id"]
        for _ in range(40):
            if answer["status"] != "working":
                break
            for action in answer["actions"]:
                assert action["runs_in"] == (CLAUDE_CODE if action["role"].startswith("engineering/") else ANTIGRAVITY)
                assert coordinator.run_step(run_id, action["invocation_id"])["status"] == "handed_in"
            answer = coordinator.run_next_action(run_id)
        assert answer["status"] == "done"
        assert any(s.startswith("claude:engineering/") for s in seen) and any(s.startswith("agy:") for s in seen)

    def test_a_desk_step_gets_an_agent_without_a_shell_from_its_own_folder(self, tmp_path: Path) -> None:
        from try_professional.adapters.agent_runner import DESK_AGENT

        step = build_step(ANTIGRAVITY, SETTINGS, tmp_path, ROLE, None, "x", tmp_path, tmp_path / "a", desk=True)
        assert step.argv[step.argv.index("--agent") + 1] == DESK_AGENT
        agent = (tmp_path / "a" / ".agents" / "agents" / f"{DESK_AGENT}.md").read_text(encoding="utf-8")
        assert "view_file" in agent and "run_command" not in agent and "search_web" not in agent
        work = build_step(ANTIGRAVITY, SETTINGS, tmp_path, ROLE, None, "x", tmp_path, tmp_path / "w")
        assert "--agent" not in work.argv, "a work step keeps every tool"

    def test_its_runs_say_who_does_each_role_and_its_steps_answer_within_three_minutes(
        self, org: Org, harness: Callable[..., Any]
    ) -> None:
        from try_professional.adapters.mcp.service import SHORT_RUN_STEP_WAIT_SECONDS

        assert SHORT_RUN_STEP_WAIT_SECONDS < 180, "Antigravity gives up on a tool call after 3 minutes"
        run = harness(org)
        plan = RuntimePlan(default=ANTIGRAVITY, models={ANTIGRAVITY: "gemini-3.8-flash-high"})
        coordinator = McpService(run.orchestrator, coordinator=True, runtimes=plan, agents=SETTINGS,
                                 root=run.orchestrator.home, client=ANTIGRAVITY)  # fmt: skip
        run_id = coordinator.run_start("Goal")["run_id"]
        rows = [json.loads(line) for line in (run.orchestrator.home / "workers.jsonl").read_text("utf-8").splitlines()]
        assert {r["worker"] for r in rows if r["run_id"] == run_id} == {"antigravity:gemini-3.8-flash-high"}

    def test_a_command_can_run_on_its_own_and_nobody_else_takes_its_steps(
        self, org: Org, harness: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hunt round's front desk spent a seventh of the run asking whether steps had finished, and stopped
        altogether at its plan's limit. Now the coordinator hands the run to a trypro process of its own."""
        from try_professional.core.lock import run_is_driven, run_lock

        run = harness(org)
        coordinator = McpService(run.orchestrator, coordinator=True, runtimes=RuntimePlan(default=ANTIGRAVITY),
                                 agents=SETTINGS, root=run.orchestrator.home, client=ANTIGRAVITY)  # fmt: skip
        driven: list[str] = []
        monkeypatch.setattr(coordinator, "_drive", driven.append)
        started = coordinator.run_in_background("Goal")
        assert started["status"] == "running" and driven == [started["run_id"]]
        run_id = started["run_id"]
        assert coordinator.run_in_background(f"continue {run_id}")["run_id"] == run_id
        with pytest.raises(ToolError, match="no run"):
            coordinator.run_in_background("continue run-0000000000ff")
        home = run.orchestrator.home
        with run_lock(home, run_id) as held:
            assert held and run_is_driven(home, run_id)
            action = next(a for a in run.orchestrator.open_actions(run_id) if a.kind == "invoke_role")
            with pytest.raises(ToolError, match="goes on by itself"):
                coordinator.run_step(run_id, action.invocation_id)
            assert coordinator.run_in_background(f"continue {run_id}")["tell"].startswith("It's already going")
        assert not run_is_driven(home, run_id)

    def test_the_front_desk_hands_work_off_and_never_polls_a_step(self, tmp_path: Path) -> None:
        from try_professional import Company
        from try_professional.adapters.mcp.front_desk import front_desk_rules

        # Both background harnesses hand work to the same runner.
        org = Company.create(tmp_path, name="Example").org
        for client in ("antigravity", "codex"):
            rules = front_desk_rules(org, client)
            assert "run_in_background" in rules and "Never call run_step" in rules

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows starts it through Task Scheduler")
    def test_on_windows_the_run_is_nobodys_child(
        self, org: Org, harness: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Antigravity ended every process its server had started when the chat ended, so the run stopped with it."""
        import subprocess as sp

        from try_professional.adapters.mcp import service

        run = harness(org)
        coordinator = McpService(run.orchestrator, coordinator=True, runtimes=RuntimePlan(default=ANTIGRAVITY),
                                 agents=SETTINGS, root=run.orchestrator.home, client=ANTIGRAVITY)  # fmt: skip
        calls: list[list[str]] = []
        monkeypatch.setattr(
            service.subprocess, "run", lambda argv, **k: calls.append(argv) or sp.CompletedProcess(argv, 0)
        )
        run_id = coordinator.run_in_background("Goal")["run_id"]
        name = f"trypro-{run_id}"
        launcher_path = run.orchestrator.home / "runs" / run_id / "driver.cmd"
        assert calls == [
            [
                "schtasks",
                "/Create",
                "/F",
                "/SC",
                "ONCE",
                "/ST",
                "00:00",
                "/TN",
                name,
                "/TR",
                f'cmd /d /s /c ""{launcher_path}""',
            ],
            ["schtasks", "/Run", "/TN", name],
        ]
        launcher = (run.orchestrator.home / "runs" / run_id / "driver.cmd").read_text(encoding="utf-8")
        assert 'set "TRYPRO_RUNTIME=antigravity"' in launcher and f'"resume" "{run_id}"' in launcher
        assert f'cd /d "{run.orchestrator.home}"' in launcher
        assert launcher.index(f'"resume" "{run_id}"') < launcher.index(f'schtasks /Delete /TN "{name}" /F')

    @pytest.mark.parametrize("failed_at", ["/Create", "/Run"])
    @pytest.mark.parametrize("failure", ["refused", "missing", "timeout"])
    def test_windows_falls_back_to_wmi_when_the_scheduler_cannot_start(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_at: str, failure: str
    ) -> None:
        import subprocess as sp

        from try_professional.adapters.mcp import service

        log = tmp_path / "run-123abc" / "driver.log"
        log.parent.mkdir()
        calls: list[list[str]] = []

        def start(argv: list[str], **kwargs: Any) -> sp.CompletedProcess[str]:
            calls.append(argv)
            assert kwargs == {"check": True, "capture_output": True, "timeout": 60}
            if argv[0] == "schtasks" and argv[1] == failed_at:
                if failure == "missing":
                    raise FileNotFoundError("schtasks")
                if failure == "timeout":
                    raise sp.TimeoutExpired(argv, 60)
                raise sp.CalledProcessError(1, argv)
            return sp.CompletedProcess(argv, 0)

        monkeypatch.setattr(service.subprocess, "run", start)
        service._start_outside_job(["trypro.exe", "resume", "run-123abc"], tmp_path, {}, log)
        assert [argv[1] for argv in calls[:-1]] == (
            ["/Create", "/Run", "/Delete"] if failed_at == "/Run" else ["/Create"]
        )
        assert calls[-1][:4] == ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
        assert "Win32_Process" in calls[-1][-1]
        launcher = log.with_name("driver.cmd").read_text("utf-8")
        assert '"trypro.exe" "resume" "run-123abc"' in launcher
        assert "schtasks /Delete" not in launcher, "the fallback does not own a scheduled task"

    def test_windows_reports_when_neither_background_launcher_can_start(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess as sp

        from try_professional.adapters.mcp import service

        def refused(argv: list[str], **kwargs: Any) -> None:
            raise sp.CalledProcessError(1, argv)

        monkeypatch.setattr(service.subprocess, "run", refused)
        with pytest.raises(sp.CalledProcessError):
            service._start_outside_job(["trypro.exe", "resume", "run-123abc"], tmp_path, {}, tmp_path / "driver.log")

    def test_posix_keeps_the_background_run_in_a_new_session(
        self, org: Org, harness: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        from try_professional.adapters.mcp import claude_code, service

        run = harness(org)
        coordinator = McpService(run.orchestrator, coordinator=True, root=run.orchestrator.home)
        calls: list[tuple[list[str], dict[str, Any]]] = []
        monkeypatch.setattr(service, "sys", SimpleNamespace(platform="linux"))
        monkeypatch.setattr(claude_code, "_command", lambda: ("python", ["-m", "try_professional.cli"]))
        monkeypatch.setattr(service.subprocess, "Popen", lambda argv, **kwargs: calls.append((argv, kwargs)))
        monkeypatch.setattr(service, "_start_outside_job", lambda *args: pytest.fail("POSIX used a Windows launcher"))
        run_id = coordinator.run_in_background("Goal")["run_id"]
        argv, kwargs = calls[0]
        assert len(calls) == 1
        assert argv == ["python", "-m", "try_professional.cli", "resume", run_id]
        assert kwargs["cwd"] == run.orchestrator.home and kwargs["start_new_session"] is True
        assert kwargs["stdin"] == service.subprocess.DEVNULL and kwargs["stderr"] == service.subprocess.STDOUT
        assert kwargs["env"] == {**os.environ, "TRYPRO_RUNTIME": coordinator.runtimes.default}

    @pytest.mark.skipif(sys.platform != "win32", reason="Exercises the Windows command interpreter")
    @pytest.mark.parametrize("exit_code", [0, 7])
    def test_windows_launcher_cleans_its_task_after_resume_succeeds_or_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int
    ) -> None:
        import subprocess as sp

        from try_professional.adapters.mcp import service

        root = tmp_path / "a company with spaces"
        root.mkdir()
        log = root / "run-123abc" / "driver.log"
        log.parent.mkdir()
        # A local batch stand-in records cleanup without creating a real scheduled task.
        (root / "schtasks.cmd").write_text(
            "@echo off\necho %* status=%trypro_exit% > deleted.txt\nexit /b %trypro_exit%\n", "utf-8"
        )
        program = root / "resume.py"
        program.write_text(
            "import os, pathlib, sys\n"
            "pathlib.Path('resumed.txt').write_text(os.environ['TRYPRO_RUNTIME'])\n"
            f"sys.exit({exit_code})\n",
            "utf-8",
        )
        # The stand-in is found in the current folder, which cmd skips when this is set (Claude Code's shell sets it).
        monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
        original_run = sp.run
        monkeypatch.setattr(service.subprocess, "run", lambda argv, **kwargs: sp.CompletedProcess(argv, 0))
        service._start_outside_job([sys.executable, str(program)], root, {"TRYPRO_RUNTIME": "codex"}, log)
        done = original_run(f'cmd /d /s /c ""{log.with_name("driver.cmd")}""',
                            cwd=tmp_path, capture_output=True, timeout=30)  # fmt: skip
        assert done.returncode == exit_code, done.stderr
        assert (root / "resumed.txt").read_text("utf-8") == "codex"
        assert (root / "deleted.txt").read_text(
            "utf-8"
        ).strip() == f'/Delete /TN "trypro-run-123abc" /F status={exit_code}'

    def test_a_role_never_searches_the_whole_machine(self, org: Org) -> None:
        """A role searched / for a phrase from its bug, and found the engine's own code, a newer version of the project."""
        assert "never across the whole machine" in role_prompt(org, org.role(ROLE), "run-1")


def test_trypro_run_records_who_did_each_role(org: Org, harness: Callable[..., Any]) -> None:
    """Started by `trypro run` rather than a coordinator, a run lent Claude's trust to Gemini roles all the same."""
    run, seen = harness(org), []
    plan = RuntimePlan(default=ANTIGRAVITY, models={ANTIGRAVITY: "gemini-3.8-flash-high"})
    runner = AgentRunner(run.orchestrator, plan, SETTINGS, root=run.orchestrator.home,
                         run_process=_fake_agents(run, seen), environ={"PATH": ""})  # fmt: skip
    result = runner.run("Goal")
    rows = [json.loads(line) for line in (run.orchestrator.home / "workers.jsonl").read_text("utf-8").splitlines()]
    assert {r["worker"] for r in rows if r["run_id"] == result.run_id} == {"antigravity:gemini-3.8-flash-high"}


def test_a_steps_time_limit_is_a_net_a_person_can_widen() -> None:
    """A hunt that examined two repositories ran past an hour, was stopped, and started over from nothing."""
    assert AgentSettings.from_environment({}).step_timeout_seconds == 3 * 3600
    assert AgentSettings.from_environment({"TRYPRO_STEP_TIMEOUT_MINUTES": "300"}).step_timeout_seconds == 300 * 60
    assert AgentSettings.from_environment({"TRYPRO_STEP_TIMEOUT_MINUTES": "x"}).step_timeout_seconds == 3 * 3600


class TestCodexOnWindows:
    """On Windows Codex's workspace-write sandbox runs no command, and a person's own ~/.codex hooks and plugins loaded
    into every step on the company's provider."""

    def test_the_sandbox_is_a_persons_choice_and_safe_by_default(self, tmp_path: Path) -> None:
        assert AgentSettings.from_environment({}).codex_sandbox == "workspace-write"
        chosen = _with_codex({"TRYPRO_CODEX_SANDBOX": "danger-full-access"})
        argv = codex_argv(chosen, tmp_path, ROLE, None)
        assert argv[argv.index("--sandbox") + 1] == "danger-full-access"
        assert codex_argv(chosen, tmp_path, ROLE, None, desk=True)[argv.index("--sandbox") + 1] == "read-only"
        assert AgentSettings.from_environment({"TRYPRO_CODEX_SANDBOX": "nonsense"}).codex_sandbox == "workspace-write"

    def test_a_step_on_the_companys_provider_has_a_codex_home_of_its_own(
        self, org: Org, harness: Callable[..., Any]
    ) -> None:
        run, envs = harness(org), []

        def capture(argv: list[str], stdin: str, env: dict[str, str], cwd: Path, timeout: int) -> tuple[int, str]:
            envs.append(env)
            return 0, ""

        runner = AgentRunner(run.orchestrator, RuntimePlan(default=CODEX), SETTINGS, root=run.orchestrator.home,
                             run_process=capture, environ={"PATH": "", "CODEX_HOME": "/home/me/.codex"})  # fmt: skip
        run_id, _ = run.orchestrator.start("Goal")
        action = next(a for a in run.orchestrator.open_actions(run_id) if a.kind == "invoke_role")
        runner._invoke(action, run_id)
        assert envs[0]["CODEX_HOME"] != "/home/me/.codex" and envs[0]["CODEX_HOME"].endswith("codex-home")

    def test_codex_printing_its_prompt_isnt_a_reply_of_no_tools(self, org: Org, harness: Callable[..., Any]) -> None:
        """Codex prints the prompt it was given, which names NO_ROLE_TOOLS, so a step that had every tool was taken
        for one that had none, and the run stopped for a person at its first step."""
        run = harness(org)

        def echoes(argv: list[str], stdin: str, env: dict[str, str], cwd: Path, timeout: int) -> tuple[int, str]:
            return 0, f"user\n{stdin}\ncodex\nI looked around and stopped.\n"

        def has_none(argv: list[str], stdin: str, env: dict[str, str], cwd: Path, timeout: int) -> tuple[int, str]:
            return 0, f"user\n{stdin}\ncodex\nNO_ROLE_TOOLS\n"

        run_id, _ = run.orchestrator.start("Goal")
        action = next(a for a in run.orchestrator.open_actions(run_id) if a.kind == "invoke_role")
        for process, expected in ((echoes, "ended (exit 0) without handing in"), (has_none, "didn't load")):
            runner = AgentRunner(run.orchestrator, RuntimePlan(default=CODEX), SETTINGS, root=run.orchestrator.home,
                                 run_process=process, environ={"PATH": ""}, sleep=lambda s: None)  # fmt: skip
            assert expected in runner._invoke(action, run_id)[0].reason

    def test_a_long_assignment_reaches_codex_whole(self, tmp_path: Path) -> None:
        """Codex cut a validator's 19k-token assignment short, and the validator lost its work package id."""
        argv = codex_argv(_with_codex({}), tmp_path, ROLE, None)
        limit = next(a for a in argv if a.startswith("tool_output_token_limit="))
        assert int(limit.split("=")[1]) >= 50_000

    def test_how_hard_codex_thinks_is_a_persons_choice(self, tmp_path: Path) -> None:
        """Left to the model, GPT-6.1 ran with no reasoning and sometimes answered that it had no tools."""
        assert not any("model_reasoning_effort" in a for a in codex_argv(_with_codex({}), tmp_path, ROLE, None))
        chosen = _with_codex({"TRYPRO_CODEX_REASONING_EFFORT": "High"})
        assert 'model_reasoning_effort="high"' in codex_argv(chosen, tmp_path, ROLE, None)

    def test_a_session_without_the_tools_is_started_again(self, org: Org, harness: Callable[..., Any]) -> None:
        """Through the owner's provider, three new Codex sessions in four had no tools, and the fourth worked."""
        run, seen, calls = harness(org), [], []
        hands_in = _fake_agents(run, seen)

        def flaky(argv: list[str], stdin: str, env: dict[str, str], cwd: Path, timeout: int) -> tuple[int, str]:
            calls.append(1)
            if len(calls) < 3:
                return 0, f"user\n{stdin}\ncodex\nNO_ROLE_TOOLS\n"
            return hands_in(argv, stdin, env, cwd, timeout)

        runner = AgentRunner(run.orchestrator, RuntimePlan(default=CODEX), SETTINGS, root=run.orchestrator.home,
                             run_process=flaky, environ={"PATH": ""}, sleep=lambda s: None)  # fmt: skip
        run_id, _ = run.orchestrator.start("Goal")
        action = next(a for a in run.orchestrator.open_actions(run_id) if a.kind == "invoke_role")
        assert runner._invoke(action, run_id) == [] and len(calls) == 3

    def test_a_long_run_of_sessions_without_tools_is_waited_out(self, org: Org, harness: Callable[..., Any]) -> None:
        """A provider passed no tools to 21 sessions of one validator in a row: six tries a step weren't enough."""
        run, seen, calls, waits = harness(org), [], [], []
        hands_in = _fake_agents(run, seen)

        def long_gap(argv: list[str], stdin: str, env: dict[str, str], cwd: Path, timeout: int) -> tuple[int, str]:
            calls.append(1)
            if len(calls) <= 21:
                return 0, f"user\n{stdin}\ncodex\nNO_ROLE_TOOLS\n"
            return hands_in(argv, stdin, env, cwd, timeout)

        runner = AgentRunner(run.orchestrator, RuntimePlan(default=CODEX), SETTINGS, root=run.orchestrator.home,
                             run_process=long_gap, environ={"PATH": ""}, sleep=waits.append)  # fmt: skip
        run_id, _ = run.orchestrator.start("Goal")
        action = next(a for a in run.orchestrator.open_actions(run_id) if a.kind == "invoke_role")
        assert runner._invoke(action, run_id) == [] and len(calls) == 22
        assert max(waits) == NO_TOOLS_MAX_BACKOFF_SECONDS and sum(waits) <= NO_TOOLS_WAIT_SECONDS

    def test_a_session_the_provider_lost_is_started_again(self, org: Org, harness: Callable[..., Any]) -> None:
        """Twice a step failed with 'The requested item was created under a different Azure OpenAI resource'."""
        run, seen, calls = harness(org), [], []
        hands_in = _fake_agents(run, seen)
        lost = '{"error":{"message":"OpenAI Responses bad request: The requested item was created under a different Azure OpenAI resource."}}'

        def flaky(argv: list[str], stdin: str, env: dict[str, str], cwd: Path, timeout: int) -> tuple[int, str]:
            calls.append(1)
            if len(calls) == 1:
                return 1, f"user\n{stdin}\nERROR: {lost}\n"
            return hands_in(argv, stdin, env, cwd, timeout)

        runner = AgentRunner(run.orchestrator, RuntimePlan(default=CODEX), SETTINGS, root=run.orchestrator.home,
                             run_process=flaky, environ={"PATH": ""}, sleep=lambda s: None)  # fmt: skip
        run_id, _ = run.orchestrator.start("Goal")
        action = next(a for a in run.orchestrator.open_actions(run_id) if a.kind == "invoke_role")
        assert runner._invoke(action, run_id) == [] and len(calls) == 2

    def test_sessions_without_tools_stop_being_retried_in_the_end(self, org: Org, harness: Callable[..., Any]) -> None:
        run, waits = harness(org), []

        def never(argv: list[str], stdin: str, env: dict[str, str], cwd: Path, timeout: int) -> tuple[int, str]:
            return 0, f"user\n{stdin}\ncodex\nNO_ROLE_TOOLS\n"

        runner = AgentRunner(run.orchestrator, RuntimePlan(default=CODEX), SETTINGS, root=run.orchestrator.home,
                             run_process=never, environ={"PATH": ""}, sleep=waits.append)  # fmt: skip
        run_id, _ = run.orchestrator.start("Goal")
        action = next(a for a in run.orchestrator.open_actions(run_id) if a.kind == "invoke_role")
        assert "didn't load" in runner._invoke(action, run_id)[0].reason
        assert NO_TOOLS_WAIT_SECONDS <= sum(waits) < NO_TOOLS_WAIT_SECONDS + NO_TOOLS_MAX_BACKOFF_SECONDS


def test_a_role_is_told_where_its_companys_files_are(org: Org, tmp_path: Path) -> None:
    """A Codex scout couldn't find deliverables/posted-reports.md, which its command named, and gave up its hunt."""
    work = role_prompt(org, org.role(ROLE), "run-1", tmp_path / "ws", company=tmp_path)
    assert f"Your company's folder, where files a command names are (such as deliverables/): {tmp_path}" in work
    assert "Your company's folder" not in role_prompt(
        org, org.role(ROLE), "run-1", tmp_path, desk=True, company=tmp_path
    )


class TestGateOnWindows:
    """A .cmd wrapper gave its arguments to cmd.exe, which split a gh api query at its "&": a scout's search broke."""

    # Windows only: pretending to be Windows elsewhere sends shutil.which down a path that needs Windows' own APIs.
    @pytest.mark.skipif(sys.platform != "win32", reason="The launcher stands in as gh.exe and git.exe on Windows")
    def test_trypros_launcher_stands_in_as_gh_and_git(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        scripts = tmp_path / "venv" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "trypro.exe").write_bytes(b"launcher")
        monkeypatch.setattr(agent_runner.sys, "executable", str(scripts / "python.exe"))
        real = tmp_path / "real"
        real.mkdir()
        for name in ("gh", "git"):
            (real / f"{name}.exe").write_text("", encoding="utf-8")
            (real / name).write_text("", encoding="utf-8")
            (real / name).chmod(0o755)
        gate = write_gate(tmp_path / "gate", tmp_path, "run-1", ROLE, tmp_path, str(real))
        assert (gate / "gh.exe").read_bytes() == b"launcher" and (gate / "git.exe").is_file()
        spec = json.loads((gate / "gate.json").read_text(encoding="utf-8"))
        assert spec["run_id"] == "run-1" and set(spec["real"]) == {"gh", "git"}

    def test_called_by_that_name_the_cli_is_the_gate(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from try_professional.adapters.agent_runner import as_gate

        (tmp_path / "gate.json").write_text(json.dumps({"company": "c", "run_id": "r", "role": ROLE, "workspace": "w",
                                                       "real": {"gh": "real-gh"}}), encoding="utf-8")  # fmt: skip
        seen: list[list[str]] = []
        monkeypatch.setattr(codex_gate, "main", lambda argv: seen.append(argv) or 0)
        query = "search/repositories?q=topic:ai-agents&per_page=1"
        assert as_gate(str(tmp_path / "gh.exe"), ["api", query]) == 0
        assert seen == [["c", "r", ROLE, "w", "real-gh", "api", query]], "the & reaches the gate as one argument"
        assert as_gate(str(tmp_path / "trypro.exe"), ["status"]) is None, "trypro by its own name is the CLI"
