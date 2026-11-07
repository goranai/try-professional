"""What `trypro doctor` checks, and the report someone can paste into a bug report.

It has to work before a company exists and when something is broken, because that's when people run it. So a check that
fails becomes a line, and never a crash. The report goes in public, so it holds counts and versions and nothing else:
no company name, no role names, no paths, no keys. `scrub` is a second layer, in case something slips into a message.
"""

from __future__ import annotations

import getpass
import importlib.metadata
import os
import platform
import re
import shutil
import socket
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import skill_customs

from . import __version__
from .adapters.api_runner.clients import ClientError
from .adapters.mcp.claude_code import agent_name
from .config import ClientRouter, find_config, load_config, load_env_file
from .generator.flow import proposals_dir
from .generator.provision import GITHUB_TOKEN_HELP, github_token_is_set
from .memory import MemoryConfigError, check_local_only
from .memory.embedding import model_cache_dir
from .org import Org, load_org
from .resources import COMMAND_PEAK_MB, free_memory_mb

OK, INFO, WARN, FAIL = "ok", "--", "warn", "!!"
CLAUDE_TIMEOUT_SECONDS = 15
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.IGNORECASE)
_MIN_SECRET_LENGTH = 8
_MIN_NAME_LENGTH = 3
_VERSIONED = ("mcp", "mnemosyne-memory", "fastembed", "anthropic")
REMOVED = "<removed>"


@dataclass(frozen=True)
class Check:
    status: str
    text: str
    fix: str = ""
    # Names a role or a model, or quotes an error, so its words stay out of a public report.
    private: bool = False


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _claude_version() -> str | None:
    """Claude Code's version, or None if it isn't installed. "unknown" if it's there and won't say."""
    found = shutil.which("claude")
    if not found:
        return None
    try:
        done = subprocess.run(
            [found, "--version"], capture_output=True, text=True, timeout=CLAUDE_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    match = re.search(r"\d+\.\d+\.\d+", done.stdout)
    return match.group() if match else "unknown"


def _setup_checks() -> list[Check]:
    checks = []
    if _package_version("mcp"):
        checks.append(Check(OK, "the MCP extra is installed"))
    else:
        checks.append(Check(FAIL, "the MCP extra isn't installed", 'pip install "try-professional[mcp]"'))
    claude = _claude_version()
    if claude:
        checks.append(Check(OK, f"Claude Code {claude}"))
    else:
        checks.append(Check(WARN, "Claude Code isn't on the PATH", "Install it from https://claude.com/claude-code"))
    if github_token_is_set():
        checks.append(Check(OK, "GITHUB_TOKEN is set, so a new company can search for skills"))
    else:
        checks.append(Check(WARN, "GITHUB_TOKEN isn't set", GITHUB_TOKEN_HELP))
    cache = model_cache_dir()
    if cache.is_dir() and any(cache.iterdir()):
        checks.append(Check(OK, "the local embedding model is downloaded"))
    else:
        checks.append(Check(INFO, "the local embedding model downloads when you approve a company, about 0.22 GB"))
    free = free_memory_mb()
    if free is None:
        checks.append(Check(INFO, "free memory: couldn't tell"))
    elif free < COMMAND_PEAK_MB:
        checks.append(
            Check(WARN, f"about {round(free / 1024, 1)} GB of memory is free, and a command needs about 0.8 GB")
        )
    else:
        checks.append(Check(OK, f"about {round(free / 1024, 1)} GB of memory is free"))
    return checks


def _waiting_proposals(root: Path) -> int:
    folder = proposals_dir(root)
    return sum(1 for path in folder.iterdir() if path.is_dir()) if folder.is_dir() else 0


def _claude_code_checks(root: Path, org: Org) -> list[Check]:
    roles = org.roles
    agents = root / ".claude" / "agents"
    made = [ref for ref in roles if (agents / f"{agent_name(ref)}.md").is_file()]
    if not made:
        return [Check(INFO, "no Claude Code subagents yet", "trypro agents claude-code")]
    if len(made) != len(roles) or not (root / ".mcp.json").is_file():
        return [Check(WARN, "the Claude Code files don't match this company", "trypro agents claude-code")]
    return [Check(OK, f"Claude Code has a subagent for all {len(roles)} roles")]


def _api_runner_checks(start: Path) -> list[Check]:
    """It's experimental, so it only counts as a problem once someone has started setting it up."""
    config = load_config(start)
    org = load_org(config.org_dir)
    env = config.model.api_key_env
    started = bool(config.model.base_url or (env and os.environ.get(env)) or os.environ.get("TRYPRO_LLM_MODEL"))
    if config.model.provider == "openai_compatible" and not started:
        return [Check(INFO, "the API runner, which is experimental, isn't set up. Claude Code doesn't need it")]
    checks = [Check(INFO, f"API runner, which is experimental: {config.model.provider}")]
    if env and not os.environ.get(env):
        checks.append(Check(FAIL, f"{env} isn't set", "Set it in .env, or in your shell"))
    if config.model.provider == "anthropic" and not _package_version("anthropic"):
        checks.append(Check(FAIL, "the anthropic SDK is missing", 'pip install "try-professional[anthropic]"'))
    if config.model.provider == "openai_compatible" and not config.model.base_url:
        checks.append(Check(FAIL, "there's no model endpoint", "Set TRYPRO_LLM_BASE_URL"))
    router = ClientRouter(config.model, org)
    try:
        for ref, role in org.roles.items():
            if org.llm_override_for(ref):
                resolved = router.resolve(role)
                variable = resolved.api_key_env
                state = "no key" if variable is None else ("set" if os.environ.get(variable) else "NOT SET")
                checks.append(Check(INFO, f"{ref}: model {resolved.model}, key {variable} ({state})", private=True))
        checks.append(Check(OK, f"default model: {config.model.model_for_tier('standard')}", private=True))
    except ClientError as error:
        checks.append(Check(FAIL, type(error).__name__, str(error), private=True))
    return checks


def _company_checks(start: Path) -> list[Check]:
    try:
        path = find_config(start)
    except FileNotFoundError:
        waiting = _waiting_proposals(start)
        if waiting:
            return [Check(INFO, f"no company yet, and {waiting} proposal(s) waiting", "trypro init --review <id>")]
        return [Check(INFO, "no company in this folder yet. See the quickstart")]
    root = path.parent
    config = load_config(start)
    org = load_org(config.org_dir)
    checks = [Check(OK, f"company: {len(org.departments)} departments, {len(org.roles)} roles")]
    checks += _claude_code_checks(root, org)
    if config.memory.enabled:
        from .cli import _memory_settings

        settings = _memory_settings(config)
        check_local_only(settings, os.environ)
        checks.append(Check(OK if not settings.remote_allowed else INFO, _memory_line(settings.remote_allowed)))
    else:
        checks.append(Check(INFO, "memory is turned off"))
    return checks + _api_runner_checks(start)


def _memory_line(remote_allowed: bool) -> str:
    return "memory: remote allowed" if remote_allowed else "memory: local only"


def _guarded(name: str, check: Callable[[Path], list[Check]], start: Path) -> list[Check]:
    """A broken project is what this is for, so it says what broke rather than failing itself."""
    try:
        return check(start)
    except MemoryConfigError as error:
        return [Check(FAIL, "memory isn't set up safely", str(error), private=True)]
    except Exception as error:  # A crash here would hide the problem someone came to find.
        return [Check(FAIL, f"couldn't check {name}: {type(error).__name__}", str(error), private=True)]


def run_checks(start: Path) -> list[Check]:
    load_env_file(start)
    return [*_setup_checks(), *_guarded("the company", _company_checks, start)]


def print_checks(checks: list[Check], say: Callable[[str], None]) -> bool:
    """Print them for a person, and say whether it's ready."""
    say(f"try-professional {__version__}, Python {platform.python_version()}")
    for check in checks:
        say(f"[{check.status}] {check.text}")
        if check.fix:
            say("\n".join(f"     {line}" for line in check.fix.splitlines()))
    ready = not any(check.status == FAIL for check in checks)
    say("Ready." if ready else "Not ready. Fix the [!!] lines.")
    return ready


def scrub(text: str, environment: dict[str, str] | None = None) -> str:
    """Take out what identifies a person or a machine: keys, paths, the user name, and the machine's name."""
    values = environment if environment is not None else dict(os.environ)
    secrets = [
        value for name, value in values.items() if _SECRET_NAME.search(name) and len(value) >= _MIN_SECRET_LENGTH
    ]
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, REMOVED)
    home, here = str(Path.home()), str(Path.cwd())
    for path in sorted({home, here, home.replace("\\", "/"), here.replace("\\", "/")}, key=len, reverse=True):
        if len(path) >= _MIN_NAME_LENGTH:
            text = text.replace(path, REMOVED)
    names = [_user_name(), socket.gethostname()]
    for name in names:
        if name and len(name) >= _MIN_NAME_LENGTH:
            text = re.sub(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", REMOVED, text, flags=re.IGNORECASE)
    return text


def _user_name() -> str:
    try:
        return getpass.getuser()
    except (OSError, KeyError, ImportError):
        return ""


def report(checks: list[Check]) -> str:
    """Safe to paste in public. Versions, the system, and the checks, with no names, paths, or keys."""
    # The code's own number, because a package's metadata is out of date in a clone.
    versions = [f"try-professional {__version__}", f"skill-customs {skill_customs.__version__}"]
    versions += [f"{name} {_package_version(name)}" for name in _VERSIONED if _package_version(name)]
    claude = _claude_version()
    lines = [
        "```text",
        "trypro doctor --report",
        f"Versions: {', '.join(versions)}",
        f"Python: {platform.python_implementation()} {platform.python_version()}",
        f"System: {platform.system()} {platform.release()}, {platform.machine()}",
        f"Claude Code: {claude or 'not found'}",
    ]
    for check in checks:
        if not check.private:
            lines.append(f"[{check.status}] {check.text}")
        elif check.status == FAIL:
            # Its words could name someone's setup, and the report still has to show that something failed.
            lines.append(f"[{FAIL}] A check failed, and its message may be private. Run trypro doctor to see it.")
    lines.append("```")
    return scrub("\n".join(lines))
