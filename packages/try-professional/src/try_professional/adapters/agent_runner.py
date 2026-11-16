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
import time
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.orchestrator import Orchestrator
from ..core.pipeline import AskHuman, Finish, IllegalEvent, InvocationFailed, InvokeRole, UsageRecorded
from ..org import Org, Role
from .api_runner.runner import COMPUTER_PURPOSES, RunResult
from .api_runner.tools import _KEPT_SECRETS, _SECRET_NAME, _stop_tree
from .cli_usage import CliUsageTracker, cli_usage
from .codex_gate import STEP_FOLDER_PREFIX
from .mcp.claude_code import (
    _FULL_TOOLS,
    _MODELS,
    _ROLE_PROMPT,
    _SANDBOXED_TOOLS,
    ROLE_SERVER,
    ROLE_TOOLS,
    server_config,
)

API, CLAUDE_CODE, CODEX, ANTIGRAVITY = "api", "claude-code", "codex", "antigravity"
RUNTIMES = (API, CLAUDE_CODE, CODEX, ANTIGRAVITY)
AGENT_CLIS = (CLAUDE_CODE, CODEX, ANTIGRAVITY)
# A net for a step that hangs, not a budget: a hunt that examined two repositories ran past an hour, was stopped,
# and started over. TRYPRO_STEP_TIMEOUT_MINUTES sets it.
STEP_TIMEOUT_SECONDS = 3 * 3600
GATED = ("gh", "git")
# A step that's neither work nor review, such as a charter, a plan or an assignment, works from its brief, as on
# trypro's own engine. A real company's first three steps each explored the repository from scratch with dozens of
# commands, and together cost more than one agent fixing the whole bug.
_DESK = (
    " This step needs no files or commands of its own: everything it needs is in your assignment and your company's "
    "tools, so don't explore."
)
# Antigravity's agent for a desk step: the company's tools, and reading a file, because Antigravity saves a long tool
# output to one. No shell, no web.
DESK_AGENT = "trypro-desk"
_DESK_AGENT = f"""---
name: {DESK_AGENT}
description: A company's desk step (a charter, a plan, an assignment). Its company's tools, and reading a file.
mainAgent: true
subagent: false
tools:
  - view_file
  - manage_task
  - wait
  - finish
---

Do the step you're given with your company's tools.
"""
_NOTE = (
    "Your company's tools are the {server} ones. A command that changes something outside, such as gh pr create or "
    "git push, waits for a person: carry on without it, and say in your submission that it's waiting."
)
# A plan's limit for now, not a failure: the agent says when it lifts, and the step is run again then. On Gemini's
# plan a company's steps hit a few-minute limit, failed one after another, and the run stopped for a person.
# Also a provider's busy hour: through the owner's reseller, "The system is currently experiencing high demand ... the
# maximum usage size allowed during peak load" ended steps that a few minutes' wait would have let finish.
# Its stream also broke off mid-answer ("stream disconnected before completion", "response.failed event received").
_LIMIT = re.compile(
    r"RESOURCE_EXHAUSTED|\b429\b|quota (reached|exceeded)|rate.?limit|usage limit|high demand|peak load"
    r"|stream disconnected before completion|response\.failed event",
    re.IGNORECASE,
)
_RESETS_IN = re.compile(r"resets? in ((?:\d+h)?\s*(?:\d+m)?\s*(?:\d+(?:\.\d+)?s)?)", re.IGNORECASE)
LIMIT_WAITS = 12
DEFAULT_LIMIT_WAIT_SECONDS = 300
# Past this, a limit is the plan's week or month, not its next few minutes: a lab step once slept toward a reset 167
# hours away. The step stops, and says so, so a person can move the agent to another account.
MAX_LIMIT_WAIT_SECONDS = 2 * 3600
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
    # Codex's sandbox for work steps. On Windows its workspace-write sandbox runs no command, so a person running it
    # there sets danger-full-access, as with Antigravity's bypass.
    codex_sandbox: str = "workspace-write"
    # How hard Codex's model thinks. Left to the model it ran with none, and GPT-6.1 then sometimes answered that it had
    # no tools without trying one.
    codex_reasoning_effort: str | None = None
    # Antigravity runs a role's commands only when this is on. Its own sandbox doesn't hold on every system.
    antigravity_bypass: bool = True
    # Antigravity agents to run steps as, when a person has set them up: one for work and review, one for every other
    # step, such as an agent with no shell or files. Antigravity has no per-run way to take tools away otherwise.
    antigravity_agent: str | None = None
    antigravity_desk_agent: str | None = None
    # gh and git behind the gate. Off only where nothing can change outside anyway, such as a container with no way
    # out: there, a role that found git wrapped went to read the gate's code, and the arms weren't alike.
    gate: bool = True
    step_timeout_seconds: int = STEP_TIMEOUT_SECONDS

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
            codex_sandbox=_codex_sandbox(environ.get("TRYPRO_CODEX_SANDBOX")),
            codex_reasoning_effort=(environ.get("TRYPRO_CODEX_REASONING_EFFORT") or "").strip().lower() or None,
            llm_model=environ.get("TRYPRO_LLM_MODEL") or None,
            antigravity_bypass=environ.get("TRYPRO_ANTIGRAVITY_BYPASS", "1").strip().lower() not in ("0", "false"),
            antigravity_agent=environ.get("TRYPRO_ANTIGRAVITY_AGENT") or None,
            antigravity_desk_agent=environ.get("TRYPRO_ANTIGRAVITY_DESK_AGENT") or None,
            gate=environ.get("TRYPRO_AGENT_GATE", "1").strip().lower() not in ("0", "false"),
            step_timeout_seconds=_minutes(environ.get("TRYPRO_STEP_TIMEOUT_MINUTES"), STEP_TIMEOUT_SECONDS),
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


def _codex_sandbox(value: str | None) -> str:
    chosen = (value or "").strip().lower()
    return chosen if chosen in ("read-only", "workspace-write", "danger-full-access") else "workspace-write"


def _minutes(value: str | None, default: int) -> int:
    try:
        return max(60, int(float(value) * 60)) if value else default
    except ValueError:
        return default


def workers_for(org: Org, plan: RuntimePlan, settings: AgentSettings) -> dict[str, str]:
    """Who does each role: its agent and model, such as "antigravity:gemini-3.8-flash-high". A run records it, so a
    role's trust stays with whoever earned it."""
    workers = {}
    for ref, role in org.roles.items():
        runtime = plan.for_role(ref)
        workers[ref] = f"{runtime}:{model_for(plan, settings, runtime, role) or 'default'}"
    return workers


def model_for(plan: RuntimePlan, settings: AgentSettings, runtime: str, role: Role) -> str | None:
    if runtime in plan.models:
        return plan.models[runtime]
    if runtime == CODEX and settings.base_url:
        return settings.llm_model
    if runtime == CLAUDE_CODE:
        return _MODELS.get(role.model_tier, "sonnet")
    return None  # the agent's own default


def role_prompt(
    org: Org,
    role: Role,
    run_id: str,
    workspace: Path | None = None,
    *,
    desk: bool = False,
    gate: bool = True,
    company: Path | None = None,
    scratch: Path | None = None,
) -> str:
    tools_line = _SANDBOXED_TOOLS + _DESK if desk else _FULL_TOOLS
    body = _ROLE_PROMPT.format(title=role.display_name, company=org.company_name, tools_line=tools_line)
    where = f"\n\nThis command's folder, for your own files and commands: {workspace}" if workspace else ""
    if scratch is not None:
        from ..core.scratch import scratch_instructions

        where += "\n\n" + scratch_instructions(scratch)
    if company is not None and not desk:
        # A step starts outside the company's folder, so a file a command names, such as deliverables/posted-reports.md,
        # wasn't found where the command said, and a scout gave up its hunt for want of it.
        where += f"\nYour company's folder, where files a command names are (such as deliverables/): {company}"
    note = _NOTE.format(server=ROLE_SERVER) if gate else f"Your company's tools are the {ROLE_SERVER} ones."
    return f"{body}\n\n{note}{where}\n\nrun_id: {run_id}\n"


CODEX_TOOL_OUTPUT_TOKENS = 100_000


def codex_argv(settings: AgentSettings, root: Path, role: str, model: str | None, *, desk: bool = False) -> list[str]:
    """`codex exec`, reading its prompt from stdin, with one role's trypro server and the company's provider."""
    server = server_config(root, role)
    argv = [
        settings.executable(CODEX), "exec", "--json", "--skip-git-repo-check", "--sandbox",
        "read-only" if desk else settings.codex_sandbox,
        "-c", "sandbox_workspace_write.network_access=true",
        "-c", f"mcp_servers.{ROLE_SERVER}.command={json.dumps(server['command'])}",
        "-c", f"mcp_servers.{ROLE_SERVER}.args={json.dumps(server['args'])}",
        "-c", f"mcp_servers.{ROLE_SERVER}.startup_timeout_sec=180",
        "-c", f"mcp_servers.{ROLE_SERVER}.tool_timeout_sec=900",
        # Codex asks before each MCP call, and `codex exec` has nobody to ask: every call to the company's tools was
        # refused, and the role answered NO_ROLE_TOOLS. These are the role's own tools; the gate holds what goes out.
        "-c", f'mcp_servers.{ROLE_SERVER}.default_tools_approval_mode="approve"',
        # Codex cuts a long tool result short: a validator's 19k-token assignment lost its work package id, and the
        # validator stopped to ask for it again.
        "-c", f"tool_output_token_limit={CODEX_TOOL_OUTPUT_TOKENS}",
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
    if settings.codex_reasoning_effort:
        argv += ["-c", f"model_reasoning_effort={json.dumps(settings.codex_reasoning_effort)}"]
    if model:
        argv += ["-m", model]
    return [*argv, "-"]


def build_step(
    runtime: str, settings: AgentSettings, root: Path, role: str, model: str | None, prompt: str,
    workspace: Path, folder: Path, *, desk: bool = False,
    scratch: Path | None = None,
) -> Step:  # fmt: skip
    """How one agent CLI runs one role's step. `folder` is the step's own, removed afterwards."""
    server = server_config(root, role)
    folder.mkdir(parents=True, exist_ok=True)
    if runtime == CODEX:
        argv = codex_argv(settings, root, role, model, desk=desk)
        extra = ["--add-dir", str(scratch)] if scratch is not None else []
        return Step([*argv[:-1], "--add-dir", str(workspace), *extra, argv[-1]], prompt, folder)
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
        agent = settings.antigravity_desk_agent if desk else settings.antigravity_agent
        if desk and not agent:
            # Antigravity reads agents from the folder it starts in too, so a desk step gets one without a shell
            # there, and the person's own settings are left alone.
            (folder / ".agents" / "agents").mkdir(exist_ok=True)
            (folder / ".agents" / "agents" / f"{DESK_AGENT}.md").write_text(_DESK_AGENT, encoding="utf-8")
            agent = DESK_AGENT
        if agent:
            argv += ["--agent", agent]
        if model:
            argv += ["--model", model]
        if scratch is not None:
            argv += ["--add-dir", str(scratch)]
        return Step(argv, "", folder)
    if runtime == CLAUDE_CODE:
        config = json.dumps({"mcpServers": {ROLE_SERVER: server}})
        tools = ",".join([f"mcp__{ROLE_SERVER}__*", *([] if desk else ROLE_TOOLS)])
        argv = [settings.executable(CLAUDE_CODE), "-p", "--output-format", "json", "--mcp-config", config,
                "--strict-mcp-config", "--allowedTools", tools, "--disable-slash-commands",
                "--add-dir", str(workspace)]  # fmt: skip
        if model:
            argv += ["--model", model]
        if scratch is not None:
            argv += ["--add-dir", str(scratch)]
        return Step(argv, prompt, folder)
    raise ValueError(f"{runtime} isn't an agent CLI")


MAX_CONTINUES = 3
# Sessions without the company's tools are started again for this long, in waits that grow to a minute. Six tries
# weren't enough: a provider passed none to 21 sessions of one validator in a row, while its other sessions had them.
NO_TOOLS_WAIT_SECONDS = 30 * 60
NO_TOOLS_BACKOFF_SECONDS = 15
NO_TOOLS_MAX_BACKOFF_SECONDS = 60
# A provider that routes requests across backends can lose a session's own items mid-step ("The requested item was
# created under a different Azure OpenAI resource"). That session can't go on, but a new one can.
_LOST_SESSION = re.compile(r"created under a different (?:Azure )?OpenAI resource", re.IGNORECASE)
MAX_NEW_SESSIONS = 3
_CONVERSATION = re.compile(r'"conversation_id"\s*:\s*"([0-9a-f-]{8,})"')
CONTINUE_PROMPT = "Continue the assignment from where you stopped, then hand it in."


def continue_step(runtime: str, step: Step, output: str) -> Step | None:
    """The same step, carrying on the agent's conversation where it stopped, or None when the agent can't."""
    found = _CONVERSATION.findall(output)
    if runtime != ANTIGRAVITY or not found or "-p" not in step.argv:
        return None
    argv = list(step.argv)
    argv[argv.index("-p") + 1] = CONTINUE_PROMPT
    return Step([argv[0], "--conversation", found[-1], *argv[1:]], step.stdin, step.cwd)


def tokens_used(runtime: str, output: str) -> int | None:
    """Only a verified input/output split establishes model tokens; text totals do not."""
    return cli_usage(runtime, output).total


def limit_wait(output: str) -> float | None:
    """Seconds to wait when an agent stopped at its plan's limit, or None when it didn't."""
    tail = output[-8000:]
    if not _LIMIT.search(tail):
        return None
    found = _RESETS_IN.search(tail)
    if not found or not found.group(1).strip():
        return float(DEFAULT_LIMIT_WAIT_SECONDS)
    seconds = 0.0
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)([hms])", found.group(1)):
        seconds += float(amount) * {"h": 3600, "m": 60, "s": 1}[unit]
    return (seconds or DEFAULT_LIMIT_WAIT_SECONDS) + 15


def step_environment(environ: Mapping[str, str], key_env: str | None, gate: Path | None) -> dict[str, str]:
    """No variable named like a secret but a GitHub token and, for Codex with the company's endpoint, its key. gh and
    git behind the gate."""
    kept = {*_KEPT_SECRETS, *([key_env] if key_env else [])}
    env = {name: value for name, value in environ.items() if not _SECRET_NAME.search(name) or name in kept}
    if gate is not None:
        env["PATH"] = str(gate) + os.pathsep + env.get("PATH", "")
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def write_gate(folder: Path, company: Path, run_id: str, role: str, workspace: Path, path: str) -> Path:
    """gh and git for one step, in front of the real ones."""
    folder.mkdir(parents=True, exist_ok=True)
    python = sys.executable
    # On Windows a .cmd wrapper hands its arguments to cmd.exe, which split a gh api query at its "&": a scout's search
    # broke. trypro's own launcher, copied as gh.exe and git.exe, passes them on as given, and the CLI, called by that
    # name, hands over to the gate described in gate.json. Without a launcher, the .cmd stays.
    launcher = Path(python).with_name("trypro.exe")
    reals: dict[str, str] = {}
    for name in GATED:
        real = shutil.which(name, path=path)
        if real is None:
            continue
        reals[name] = real
        if sys.platform == "win32" and launcher.is_file():
            shutil.copyfile(launcher, folder / f"{name}.exe")
        # Isolated (-I): a project's PYTHONPATH, set to run its own tests, once put another try_professional first, and
        # git, called by those tests, failed. The role then went to read the gate's code to see why.
        fixed = [python, "-I", "-m", "try_professional.adapters.codex_gate", str(company), run_id, role, str(workspace),
                 real]  # fmt: skip
        if sys.platform == "win32":
            line = " ".join(f'"{part}"' for part in fixed)
            (folder / f"{name}.cmd").write_text(f"@echo off\r\n{line} %*\r\n", encoding="utf-8")
        script = folder / name
        quoted = " ".join("'" + part.replace("'", "'\\''") + "'" for part in fixed)
        script.write_text(f'#!/bin/sh\nexec {quoted} "$@"\n', encoding="utf-8", newline="\n")
        script.chmod(0o755)
    gate = {"company": str(company), "run_id": run_id, "role": role, "workspace": str(workspace), "real": reals}
    (folder / GATE_SPEC).write_text(json.dumps(gate), encoding="utf-8")
    return folder


GATE_SPEC = "gate.json"


def as_gate(argv0: str, args: list[str]) -> int | None:
    """When trypro's launcher runs as gh.exe or git.exe in a gate's folder, the gate for that command. Else None."""
    invoked = Path(argv0)
    name = invoked.stem.lower()
    spec = invoked.parent / GATE_SPEC
    if name not in GATED or not spec.is_file():
        return None
    gate = json.loads(spec.read_text(encoding="utf-8"))
    real = gate["real"].get(name)
    if not real:
        return None
    from .codex_gate import main as gate_main

    return gate_main([gate["company"], gate["run_id"], gate["role"], gate["workspace"], real, *args])


def _run(argv: list[str], stdin: str, env: dict[str, str], cwd: Path, timeout: int) -> tuple[int, str]:
    """In its own process group, so a time limit stops the agent and everything it started, its servers too.

    On Windows in its own hidden console as well: a Ctrl+C an agent sends to stop one of its commands goes to every
    process on the console, and twice it stopped the run's driver mid-round with a KeyboardInterrupt.
    """
    group: dict[str, Any] = (
        {
            "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        }
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
    sleep: Callable[[float], None] = time.sleep,
) -> list[Any]:
    """One role's step in an agent CLI. Returns nothing when the role handed in through its tools, or why it didn't.
    A step stopped by the plan's limit waits until it lifts and runs again."""
    role = orchestrator.org.role(action.role)
    workspace = orchestrator.workspace(run_id)
    workspace.mkdir(parents=True, exist_ok=True)
    # Outside the company's folder, so the agent loads none of the company's own files.
    scratch = orchestrator.scratch(run_id)
    # A real Windows agent holds its cwd open, so its startup folder must not block terminal scratch cleanup.
    folder = Path(tempfile.mkdtemp(prefix=f"{STEP_FOLDER_PREFIX}{action.invocation_id}-"))
    environ = dict(environ if environ is not None else os.environ)
    key_env = settings.key_env if runtime == CODEX and settings.base_url else None
    desk = action.purpose not in COMPUTER_PURPOSES
    tracker = CliUsageTracker()
    waited = 0.0
    code: int | None = None
    output = ""
    model: str | None = None
    package_id: str | None = None

    def record_usage(printed: str, *, incomplete: bool = False) -> None:
        usage = tracker.read(runtime, printed, exit_code=None if incomplete else code)
        orchestrator.record(
            run_id,
            "cli_usage_evidence",
            {
                "invocation_id": action.invocation_id,
                "runtime": runtime,
                "exit": code,
                "status": usage.status,
                "source": usage.source,
                "note": usage.note,
                "reported": usage.reported,
            },
        )
        orchestrator.handle(
            run_id,
            UsageRecorded(
                invocation_id=action.invocation_id,
                role=action.role,
                purpose=action.purpose.value,
                work_package_id=package_id,
                model=model or runtime,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                raw_input_tokens=usage.raw_input_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens,
                input_tokens_includes_cache=True,
                raw_input_tokens_includes_cache=usage.raw_input_tokens_includes_cache,
                usage_status="unavailable" if incomplete or usage.status == "partial" else usage.status,
                usage_source=usage.source,
                usage_note="The CLI timed out or could not finish; complete usage is unavailable."
                if incomplete
                else usage.note,
                calls=None,
            ),
        )

    try:
        model = model_for(plan, settings, runtime, role)
        starting_state = orchestrator.state(run_id)
        invocation = starting_state.invocations.get(action.invocation_id)
        task = starting_state.tasks.get(action.task_id or "")
        package_id = (invocation.work_package_id if invocation else None) or (task.work_package_id if task else None)
        gate = (write_gate(folder / "bin", root, run_id, action.role, workspace, environ.get("PATH", ""))
                if settings.gate else None)  # fmt: skip
        prompt = role_prompt(orchestrator.org, role, run_id, workspace, desk=desk, gate=settings.gate, company=root,
                             scratch=scratch)  # fmt: skip
        step = build_step(runtime, settings, root, action.role, model, prompt,
                          workspace,
                          folder / "start", desk=desk, scratch=scratch)  # fmt: skip
        env = step_environment(environ, key_env, gate)
        env["TRYPRO_RUN_SCRATCH"] = str(scratch)
        if key_env is not None:
            # Codex on the company's own provider: none of the person's own Codex settings, hooks or plugins.
            home = folder / "codex-home"
            home.mkdir(exist_ok=True)
            env["CODEX_HOME"] = str(home)
        waited = 0.0
        continued = 0
        first = step
        attempt = 0
        no_tools = 0
        no_tools_waited = 0
        new_sessions = 0
        while attempt <= LIMIT_WAITS:
            code, output = run_process(step.argv, step.stdin, env, step.cwd, settings.step_timeout_seconds)
            record_usage(output)
            if action.invocation_id not in orchestrator.state(run_id).invocations:
                break
            # A provider can pass the company's tools to some sessions and not others: through one, three new
            # sessions in four had none and answered NO_ROLE_TOOLS, and the fourth worked to the end. A new session
            # is cheap; asking a person isn't. It goes by request, not by account: some sessions saw none of the
            # company's tools, and one saw only a tool of the provider's own.
            if "NO_ROLE_TOOLS" in output.replace(prompt, "") and no_tools_waited < NO_TOOLS_WAIT_SECONDS:
                no_tools += 1
                # Spread out: once, seven sessions in two minutes all came up without the tools.
                pause = min(NO_TOOLS_BACKOFF_SECONDS * no_tools, NO_TOOLS_MAX_BACKOFF_SECONDS)
                sleep(pause)
                no_tools_waited += pause
                step = first
                continue
            if _LOST_SESSION.search(output) and new_sessions < MAX_NEW_SESSIONS:
                new_sessions += 1
                sleep(NO_TOOLS_BACKOFF_SECONDS * new_sessions)
                step = first
                continue
            attempt += 1
            wait = limit_wait(output)
            if wait is not None and wait > MAX_LIMIT_WAIT_SECONDS:
                hours = round(wait / 3600)
                return [_failed(action, f"{runtime}'s plan has reached its limit, which lifts in about {hours} hours. "
                                        f"Sign {runtime} in to another account, then resume the run.",
                                retryable=False)]  # fmt: skip
            # Stopped short, by a limit or the agent's own service failing: the same conversation goes on, so what
            # was done isn't done again. A hunter lost 241k tokens of work to one "server_shutting_down".
            again = continue_step(runtime, first, output)
            if wait is None and (again is None or continued >= MAX_CONTINUES or "AGY_ERROR" not in output):
                break
            if attempt > LIMIT_WAITS:
                break
            if wait is not None:
                sleep(wait)
                waited += wait
            else:
                continued += 1
            step = again or first
    except subprocess.TimeoutExpired as error:
        partial = error.output or ""
        output = partial.decode("utf-8", errors="replace") if isinstance(partial, bytes) else partial
        record_usage(output, incomplete=True)
        return [
            _failed(
                action,
                f"{runtime} didn't finish within {settings.step_timeout_seconds // 60} minutes. "
                "TRYPRO_STEP_TIMEOUT_MINUTES gives a step longer.",
            )
        ]
    except (OSError, ValueError) as error:
        record_usage("", incomplete=True)
        return [_failed(action, f"{runtime} couldn't start: {error}", retryable=False)]
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        usage = orchestrator.state(run_id).usage_by_invocation.get(action.invocation_id)
        orchestrator.record(
            run_id,
            "agent_step",
            {
                "invocation_id": action.invocation_id,
                "role": action.role,
                "runtime": runtime,
                "exit": code,
                "desk": desk,
                "limit_waited_seconds": waited,
                "tokens_used": usage.total if usage and usage.model_usage_status == "exact" else None,
                "usage_status": usage.model_usage_status if usage else "unavailable",
                "usage": usage.model_dump() if usage else None,
            },
        )
    logs = environ.get("TRYPRO_AGENT_STEP_LOGS")
    if logs:
        # What the agent printed, for a person finding out why a step went as it did, with the provider key taken out.
        secret = environ.get(key_env) if key_env else None
        kept = output.replace(secret, "[key]") if secret else output
        target = Path(logs) / run_id / f"{action.invocation_id}-{action.role.replace('/', '-')}.log"
        with contextlib.suppress(OSError):
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(kept + chr(10))
    if action.invocation_id not in orchestrator.state(run_id).invocations:
        return []  # handed in through its tools
    # Codex prints the prompt it was given, and the prompt names NO_ROLE_TOOLS: only the agent's own reply counts.
    if "NO_ROLE_TOOLS" in output.replace(prompt, ""):
        # Once more before a person is asked: a model on Codex sometimes took a prefixed tool name for a missing one.
        return [_failed(action, f"{runtime} didn't load the role's trypro tools.", retryable=True)]
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
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.sleep = sleep
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
        run_id, _ = self.orchestrator.start(goal, run_id, workers=workers_for(self.org, self.plan, self.settings))
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
                              settings=self.settings, run_process=self.run_process, environ=self.environ,
                              sleep=self.sleep)  # fmt: skip

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
            model_usage_status=state.usage.model_usage_status,
            estimated_tool_tokens=state.usage.estimated_tool_tokens,
            unknown_model_calls=state.usage.unknown_model_calls,
            unavailable_usage_records=state.usage.unavailable_records,
            unverified_usage_records=state.usage.unverified_records,
            unverified_legacy_tokens=state.usage.unverified_legacy_tokens,
        )
