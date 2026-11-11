"""Run each role's steps in the agent it's set to: Claude Code, Codex, Antigravity, or trypro's own API runner.

The company stays one: its memory, reviews, trust and record live here, whoever does a step. A role's step in an
agent CLI is one non-interactive run of it (`claude -p`, `codex exec`, `agy -p`), given only that role's trypro tools:
the same role prompt and the same server a Claude Code subagent gets. Roles can be mixed, so a company can, say,
coordinate in Claude Code, look for bugs with Gemini in Antigravity, and fix them with GPT in Codex:

    runtime:
      default: claude-code
      roles:
        research: antigravity          # a department
        engineering/backend: codex     # one role
    models:
      antigravity: gemini-3.8-flash-high

What an agent runs, it runs with nobody to ask, so whatever the agent:
- gh and git go through codex_gate: what only reads runs, and anything else waits for a person.
- Variables named like keys, tokens, secrets or passwords reach neither the agent nor its commands, apart from a GitHub
  token, which gh needs to read, as on the API runner, and the company's own provider key, which only Codex is given,
  when the company has an endpoint, and keeps from the commands it runs.
- A step starts in a folder of its own outside the company, so the agent loads none of the company's own files, such
  as the coordinator's .mcp.json or the front desk in CLAUDE.md.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.orchestrator import Orchestrator
from ..core.pipeline import AskHuman, Finish, IllegalEvent, InvocationFailed, InvokeRole
from ..org import Org, Role
from .api_runner.runner import RunResult
from .api_runner.tools import _KEPT_SECRETS, _SECRET_NAME, _stop_tree
from .codex_gate import STEP_FOLDER_PREFIX
from .mcp.claude_code import _FULL_TOOLS, _MODELS, _ROLE_PROMPT, ROLE_SERVER, ROLE_TOOLS, server_config

API, CLAUDE_CODE, CODEX, ANTIGRAVITY = "api", "claude-code", "codex", "antigravity"
RUNTIMES = (API, CLAUDE_CODE, CODEX, ANTIGRAVITY)
AGENT_CLIS = (CLAUDE_CODE, CODEX, ANTIGRAVITY)
STEP_TIMEOUT_SECONDS = 3600
GATED = ("gh", "git")
_NOTE = (
    "Your company's tools are the {server} ones. A command that changes something outside, such as gh pr create or "
    "git push, waits for a person: carry on without it, and say in your submission that it's waiting."
)
_CODEX_TOKENS = re.compile(r"tokens used\s*\n?\s*([\d,]+)", re.IGNORECASE)
# What Codex itself leaves out of a command's environment, on top of the provider's key.
_KEPT_FROM_COMMANDS = ["*API_KEY*", "*SECRET*", "*PASSWORD*", "*_AUTH*", "TRYPRO_*", "OPENAI_*", "ANTHROPIC_*"]

RunProcess = Callable[[list[str], str, dict[str, str], Path, int], tuple[int, str]]


# ---- which agent does which role --------------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimePlan:
    default: str = API
    roles: dict[str, str] = field(default_factory=dict)  # a role, such as qa/reviewer, or a department, such as qa
    models: dict[str, str] = field(default_factory=dict)  # a runtime's model, for every role it does

    def for_role(self, role: str) -> str:
        return self.roles.get(role) or self.roles.get(role.split("/", 1)[0]) or self.default

    def uses(self, runtime: str, org: Org) -> bool:
        return any(self.for_role(role) == runtime for role in org.roles)

    @classmethod
    def from_config(cls, raw: Mapping[str, Any], environ: Mapping[str, str] = os.environ) -> RuntimePlan:
        """runtime in trypro.yaml, a name or {default, roles}, and models. TRYPRO_RUNTIME in .env sets the default."""
        value = raw.get("runtime")
        section: Mapping[str, Any] = value if isinstance(value, Mapping) else {"default": value}
        default = _runtime(environ.get("TRYPRO_RUNTIME") or section.get("default") or API)
        roles = {str(name): _runtime(runtime) for name, runtime in (section.get("roles") or {}).items()}
        models = {_runtime(name): str(model) for name, model in (raw.get("models") or {}).items()}
        return cls(default=default, roles=roles, models=models)


def _runtime(name: Any) -> str:
    runtime = str(name).strip().lower().replace("_", "-")
    if runtime not in RUNTIMES:
        raise ValueError(f'Unknown runtime "{name}". Use one of: {", ".join(RUNTIMES)}.')
    return runtime


# ---- one step in an agent CLI -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentSettings:
    """Where each agent CLI is, and the model provider Codex uses when the company has its own endpoint."""

    codex: str | None = None
    antigravity: str | None = None
    claude: str | None = None
    base_url: str | None = None
    key_env: str = "TRYPRO_LLM_API_KEY"
    wire_api: str = "responses"
    llm_model: str | None = None
    # Antigravity runs a role's commands only when this is on. Its own sandbox doesn't hold on every system.
    antigravity_bypass: bool = True

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] = os.environ, key_env: str = "TRYPRO_LLM_API_KEY"
    ) -> AgentSettings:
        """key_env is the variable the company's own provider key is in: model.api_key_env in trypro.yaml."""
        beside = Path(environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
        return cls(
            codex=shutil.which("codex"),
            antigravity=shutil.which("agy") or (str(beside) if beside.is_file() else None),
            claude=shutil.which("claude"),
            base_url=environ.get("TRYPRO_LLM_BASE_URL") or None,
            key_env=key_env,
            wire_api=environ.get("TRYPRO_CODEX_WIRE_API") or "responses",
            llm_model=environ.get("TRYPRO_LLM_MODEL") or None,
            antigravity_bypass=environ.get("TRYPRO_ANTIGRAVITY_BYPASS", "1").strip().lower() not in ("0", "false"),
        )

    def executable(self, runtime: str) -> str:
        found = {CODEX: self.codex, ANTIGRAVITY: self.antigravity, CLAUDE_CODE: self.claude}[runtime]
        if not found:
            install = {CODEX: "npm install -g @openai/codex",
                       ANTIGRAVITY: "see https://antigravity.google/docs/install?tab=cli",
                       CLAUDE_CODE: "see https://code.claude.com"}[runtime]  # fmt: skip
            raise FileNotFoundError(f"{runtime} isn't installed here. Install it: {install}")
        return found


@dataclass(frozen=True)
class Step:
    argv: list[str]
    stdin: str
    cwd: Path


def model_for(plan: RuntimePlan, settings: AgentSettings, runtime: str, role: Role) -> str | None:
    if runtime in plan.models:
        return plan.models[runtime]
    if runtime == CODEX and settings.base_url:
        return settings.llm_model
    if runtime == CLAUDE_CODE:
        return _MODELS.get(role.model_tier, "sonnet")
    return None  # the agent's own default


def role_prompt(org: Org, role: Role, run_id: str, workspace: Path | None = None) -> str:
    body = _ROLE_PROMPT.format(title=role.display_name, company=org.company_name, tools_line=_FULL_TOOLS)
    where = f"\n\nThis command's folder, for your own files and commands: {workspace}" if workspace else ""
    return f"{body}\n\n{_NOTE.format(server=ROLE_SERVER)}{where}\n\nrun_id: {run_id}\n"


def codex_argv(settings: AgentSettings, root: Path, role: str, model: str | None) -> list[str]:
    """`codex exec`, reading its prompt from stdin, with one role's trypro server and the company's provider."""
    server = server_config(root, role)
    argv = [
        settings.executable(CODEX), "exec", "--skip-git-repo-check", "--sandbox", "workspace-write",
        "-c", "sandbox_workspace_write.network_access=true",
        "-c", f"mcp_servers.{ROLE_SERVER}.command={json.dumps(server['command'])}",
        "-c", f"mcp_servers.{ROLE_SERVER}.args={json.dumps(server['args'])}",
        "-c", f"mcp_servers.{ROLE_SERVER}.startup_timeout_sec=180",
        "-c", f"mcp_servers.{ROLE_SERVER}.tool_timeout_sec=900",
        "-c", "shell_environment_policy.ignore_default_excludes=true",
        "-c", f"shell_environment_policy.exclude={json.dumps([*_KEPT_FROM_COMMANDS, settings.key_env])}",
    ]  # fmt: skip
    if settings.base_url:
        argv += [
            "-c", 'model_provider="trypro"',
            "-c", 'model_providers.trypro.name="trypro"',
            "-c", f"model_providers.trypro.base_url={json.dumps(settings.base_url)}",
            "-c", f"model_providers.trypro.env_key={json.dumps(settings.key_env)}",
            "-c", f"model_providers.trypro.wire_api={json.dumps(settings.wire_api)}",
        ]  # fmt: skip
    if model:
        argv += ["-m", model]
    return [*argv, "-"]


def build_step(
    runtime: str, settings: AgentSettings, root: Path, role: str, model: str | None, prompt: str,
    workspace: Path, folder: Path,
) -> Step:  # fmt: skip
    """How one agent CLI runs one role's step. `folder` is the step's own, removed afterwards."""
    server = server_config(root, role)
    folder.mkdir(parents=True, exist_ok=True)
    if runtime == CODEX:
        argv = codex_argv(settings, root, role, model)
        return Step([*argv[:-1], "--add-dir", str(workspace), argv[-1]], prompt, folder)
    if runtime == ANTIGRAVITY:
        # Antigravity reads its MCP servers from the folder it starts in, so each step starts in its own.
        (folder / ".agents").mkdir(exist_ok=True)
        entry = {k: v for k, v in server.items() if k != "type"}
        (folder / ".agents" / "mcp_config.json").write_text(
            json.dumps({"mcpServers": {ROLE_SERVER: entry}}, indent=2), encoding="utf-8"
        )
        argv = [settings.executable(ANTIGRAVITY), "-p", prompt, "--output-format", "json", "--disable-slash-commands",
                "--add-dir", str(workspace)]  # fmt: skip
        if settings.antigravity_bypass:
            argv.append("--dangerously-skip-permissions")
        if model:
            argv += ["--model", model]
        return Step(argv, "", folder)
    if runtime == CLAUDE_CODE:
        config = json.dumps({"mcpServers": {ROLE_SERVER: server}})
        tools = ",".join([f"mcp__{ROLE_SERVER}__*", *ROLE_TOOLS])
        argv = [settings.executable(CLAUDE_CODE), "-p", "--output-format", "json", "--mcp-config", config,
                "--strict-mcp-config", "--allowedTools", tools, "--disable-slash-commands",
                "--add-dir", str(workspace)]  # fmt: skip
        if model:
            argv += ["--model", model]
        return Step(argv, prompt, folder)
    raise ValueError(f"{runtime} isn't an agent CLI")


def tokens_used(runtime: str, output: str) -> int | None:
    if runtime == CODEX:
        found = _CODEX_TOKENS.search(output)
        return int(found.group(1).replace(",", "")) if found else None
    for line in reversed(output.strip().splitlines()):
        try:
            usage = json.loads(line).get("usage") or {}
        except (ValueError, AttributeError):
            continue
        total = usage.get("total_tokens")
        if total is None and usage:
            total = sum(int(usage.get(k) or 0) for k in ("input_tokens", "output_tokens"))
        return int(total) if total is not None else None
    return None


def step_environment(environ: Mapping[str, str], key_env: str | None, gate: Path) -> dict[str, str]:
    """No variable named like a secret but a GitHub token and, for Codex with the company's endpoint, its key. gh and
    git behind the gate."""
    kept = {*_KEPT_SECRETS, *([key_env] if key_env else [])}
    env = {name: value for name, value in environ.items() if not _SECRET_NAME.search(name) or name in kept}
    env["PATH"] = str(gate) + os.pathsep + env.get("PATH", "")
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def write_gate(folder: Path, company: Path, run_id: str, role: str, workspace: Path, path: str) -> Path:
    """gh and git for one step, in front of the real ones."""
    folder.mkdir(parents=True, exist_ok=True)
    python = sys.executable
    for name in GATED:
        real = shutil.which(name, path=path)
        if real is None:
            continue
        fixed = [python, "-m", "try_professional.adapters.codex_gate", str(company), run_id, role, str(workspace), real]
        if sys.platform == "win32":
            line = " ".join(f'"{part}"' for part in fixed)
            (folder / f"{name}.cmd").write_text(f"@echo off\r\n{line} %*\r\n", encoding="utf-8")
        script = folder / name
        quoted = " ".join("'" + part.replace("'", "'\\''") + "'" for part in fixed)
        script.write_text(f'#!/bin/sh\nexec {quoted} "$@"\n', encoding="utf-8", newline="\n")
        script.chmod(0o755)
    return folder


def _run(argv: list[str], stdin: str, env: dict[str, str], cwd: Path, timeout: int) -> tuple[int, str]:
    """In its own process group, so a time limit stops the agent and everything it started, its servers too."""
    group: dict[str, Any] = (
        {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    process = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=cwd, text=True,
        encoding="utf-8", errors="replace", **group,
    )  # fmt: skip
    try:
        out, err = process.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop_tree(process)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.communicate(timeout=10)
        raise
    return process.returncode, (out or "") + "\n" + (err or "")


def run_agent_step(
    orchestrator: Orchestrator,
    action: InvokeRole,
    run_id: str,
    runtime: str,
    *,
    root: Path,
    plan: RuntimePlan,
    settings: AgentSettings,
    run_process: RunProcess = _run,
    environ: Mapping[str, str] | None = None,
) -> list[Any]:
    """One role's step in an agent CLI. Returns nothing when the role handed in through its tools, or why it didn't."""
    role = orchestrator.org.role(action.role)
    workspace = orchestrator.workspace(run_id)
    workspace.mkdir(parents=True, exist_ok=True)
    # Outside the company's folder, so the agent loads none of the company's own files.
    folder = Path(tempfile.mkdtemp(prefix=f"{STEP_FOLDER_PREFIX}{action.invocation_id}-"))
    environ = dict(environ if environ is not None else os.environ)
    key_env = settings.key_env if runtime == CODEX and settings.base_url else None
    try:
        gate = write_gate(folder / "bin", root, run_id, action.role, workspace, environ.get("PATH", ""))
        step = build_step(runtime, settings, root, action.role, model_for(plan, settings, runtime, role),
                          role_prompt(orchestrator.org, role, run_id, workspace), workspace,
                          folder / "start")  # fmt: skip
        code, output = run_process(step.argv, step.stdin, step_environment(environ, key_env, gate),
                                   step.cwd, STEP_TIMEOUT_SECONDS)  # fmt: skip
    except subprocess.TimeoutExpired:
        return [_failed(action, f"{runtime} didn't finish within {STEP_TIMEOUT_SECONDS // 60} minutes.")]
    except (OSError, ValueError) as error:
        return [_failed(action, f"{runtime} couldn't start: {error}", retryable=False)]
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    orchestrator.record(run_id, "agent_step", {
        "invocation_id": action.invocation_id, "role": action.role, "runtime": runtime, "exit": code,
        "tokens_used": tokens_used(runtime, output),
    })  # fmt: skip
    if action.invocation_id not in orchestrator.state(run_id).invocations:
        return []  # handed in through its tools
    if "NO_ROLE_TOOLS" in output:
        return [_failed(action, f"{runtime} didn't load the role's trypro tools.", retryable=False)]
    tail = " ".join(output.strip().splitlines()[-3:])[-300:]
    return [_failed(action, f"{runtime} ended (exit {code}) without handing in: {tail}")]


def _failed(action: InvokeRole, reason: str, *, retryable: bool = True) -> InvocationFailed:
    return InvocationFailed(invocation_id=action.invocation_id, reason=reason, retryable=retryable)


# ---- a whole command, coordinated by trypro -----------------------------------------------------------------------


class AgentRunner:
    """Drives a command to its end, each step in its role's agent. Roles on the API runner go to `api`."""

    name = "agents"

    def __init__(
        self,
        orchestrator: Orchestrator,
        plan: RuntimePlan,
        settings: AgentSettings,
        *,
        root: Path,
        api: Any = None,
        run_process: RunProcess = _run,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.org: Org = orchestrator.org
        self.plan = plan
        self.settings = settings
        self.root = root
        self.api = api
        self.run_process = run_process
        self.environ = environ
        # As on the API runner. Nobody answers inside a step, so a change outside always waits: `trypro held`.
        self.approve: Any = None
        self.hold = True
        # Answers a question the run stops on, as on the API runner, as in `trypro chat`.
        self.on_human: Callable[[AskHuman], Any] | None = None

    def run(self, goal: str, run_id: str | None = None) -> RunResult:
        run_id, _ = self.orchestrator.start(goal, run_id)
        return self.resume(run_id)

    def resume(self, run_id: str) -> RunResult:
        """Until finished or blocked. One step per role at a time: a role's server hands it all of its work."""
        limit = self.org.pipeline.max_parallel_tasks
        in_flight: dict[Future[list[Any]], InvokeRole] = {}
        with ThreadPoolExecutor(max_workers=limit, thread_name_prefix="trypro-agents") as pool:
            while True:
                actions = self.orchestrator.open_actions(run_id)
                busy = {action.role for action in in_flight.values()}
                for action in actions:
                    if isinstance(action, InvokeRole) and action.role not in busy and len(in_flight) < limit:
                        in_flight[pool.submit(self._invoke, action, run_id)] = action
                        busy.add(action.role)
                if not in_flight:
                    finish = next((a for a in actions if isinstance(a, Finish)), None)
                    if finish is not None:
                        return self._result(run_id, finish.status)
                    questions = [a for a in actions if isinstance(a, AskHuman)]
                    if questions:
                        if self._answer(run_id, questions):
                            continue
                        return self._result(run_id, "blocked", questions)
                    return self._result(run_id, "blocked", reason="no actions are available")
                done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for future in done:
                    in_flight.pop(future)
                    for event in future.result():
                        # Already audited: a step that ended after its run finished lands here.
                        with contextlib.suppress(IllegalEvent):
                            self.orchestrator.handle(run_id, event)

    def _invoke(self, action: InvokeRole, run_id: str) -> list[Any]:
        runtime = self.plan.for_role(action.role)
        if runtime == API:
            if self.api is None:
                return [_failed(action, "This role is set to the API runner, and no model endpoint is set up.",
                                retryable=False)]  # fmt: skip
            events: list[Any] = self.api._invoke(action, run_id)
            return events
        return run_agent_step(self.orchestrator, action, run_id, runtime, root=self.root, plan=self.plan,
                              settings=self.settings, run_process=self.run_process, environ=self.environ)  # fmt: skip

    def _answer(self, run_id: str, questions: list[AskHuman]) -> bool:
        if self.on_human is None:
            return False
        answered = False
        for question in questions:
            decision = self.on_human(question)
            if decision is not None:
                self.orchestrator.handle(run_id, decision)
                answered = True
        return answered

    def _result(
        self, run_id: str, status: str, questions: list[AskHuman] | None = None, reason: str | None = None
    ) -> RunResult:
        state = self.orchestrator.state(run_id)
        final = state.artifacts.get(state.final_artifact_id or "")
        return RunResult(
            run_id=run_id,
            status=status,
            final=final.payload if final and status == "done" else None,
            reason=reason or state.failure_reason,
            questions=questions or [],
            input_tokens=state.usage.input_tokens,
            output_tokens=state.usage.output_tokens,
            cached_input_tokens=state.usage.cached_input_tokens,
            calls=state.usage.calls,
            size=state.size,
        )
