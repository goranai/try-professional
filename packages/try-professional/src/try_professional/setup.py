"""`trypro setup claude-code`: everything before designing a company, in one step. Approval stays a person's.

It checks what the quickstart needs, adds the coordinator's MCP server to Claude Code for this folder, and remembers
that this folder runs on Claude Code. Then approving a company also writes its role subagents, so a person doesn't run
`trypro agents claude-code` by hand. It never approves anything, and never writes a key to a file.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from .adapters.mcp.claude_code import server_config
from .config import load_env_file
from .doctor import _claude_version, _package_version
from .generator.provision import GITHUB_TOKEN_HELP, github_token_is_set

RUNTIME_FILE = "runtime.json"
CLAUDE_CODE = "claude-code"
SERVER_NAME = "trypro"
_TIMEOUT_SECONDS = 60


def runtime_of(root: Path) -> str | None:
    """Which runtime `trypro setup` set up here, if any."""
    try:
        data = json.loads((root / ".trypro" / RUNTIME_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    runtime = data.get("runtime")
    return runtime if isinstance(runtime, str) else None


def _remember(root: Path) -> None:
    home = root / ".trypro"
    home.mkdir(parents=True, exist_ok=True)
    (home / ".gitignore").write_text("*\n", encoding="utf-8", newline="\n")
    (home / RUNTIME_FILE).write_text(json.dumps({"runtime": CLAUDE_CODE}) + "\n", encoding="utf-8", newline="\n")


def add_server(root: Path) -> tuple[bool, str]:
    """Add the coordinator to Claude Code, for this folder only. A server that's already there counts as done."""
    claude = shutil.which("claude")
    if claude is None:
        return False, "Claude Code isn't on the PATH."
    config = server_config(root)
    existing = subprocess.run(
        [claude, "mcp", "get", SERVER_NAME], cwd=root, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS
    )
    if existing.returncode == 0:
        if str(root).replace("\\", "/") in existing.stdout.replace("\\", "/"):
            return True, "The trypro server was already added for this folder."
        return False, (
            "Claude Code already has a server named trypro, for another folder. Remove it with "
            "`claude mcp remove trypro`, and run this again."
        )
    done = subprocess.run(
        [claude, "mcp", "add", SERVER_NAME, "--", config["command"], *config["args"]],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
    )
    if done.returncode != 0:
        return False, f"Claude Code couldn't add the server: {(done.stderr or done.stdout).strip()[:300]}"
    return True, "Added the trypro server to Claude Code, for this folder."


def setup_claude_code(
    root: Path, say: Callable[[str], None], add: Callable[[Path], tuple[bool, str]] = add_server
) -> int:
    root.mkdir(parents=True, exist_ok=True)
    load_env_file(root)
    ready = True
    # pip refuses to install this on anything older than 3.10, so reaching here means it's new enough.
    say(f"[ok] Python {sys.version.split()[0]}")
    if _package_version("mcp"):
        say("[ok] the MCP extra is installed")
    else:
        say('[!!] the MCP extra isn\'t installed. Run: pip install "try-professional[mcp]"')
        ready = False
    claude = _claude_version()
    if claude:
        say(f"[ok] Claude Code {claude}")
    else:
        say("[!!] Claude Code isn't on the PATH. Install it from https://claude.com/claude-code, and run this again.")
        ready = False
    token = github_token_is_set()
    if token:
        say("[ok] GITHUB_TOKEN is set, so your company can get skills")
    else:
        say("[warn] GITHUB_TOKEN isn't set")
        say("\n".join(f"     {line}" for line in GITHUB_TOKEN_HELP.splitlines()))
    if not ready:
        say("Nothing was added. Fix the [!!] lines, and run this again.")
        return 2
    added, message = add(root)
    say(f"[{'ok' if added else '!!'}] {message}")
    if not added:
        return 1
    _remember(root)
    say("")
    say("Next:")
    step = 1
    if not token:
        say(f"  {step}. Set GITHUB_TOKEN as above, in the shell you'll start Claude Code from.")
        step += 1
    say(f'  {step}. claude "Use the trypro tools to design a company for <your business>."')
    say(f"  {step + 1}. trypro init --review <proposal-id>    Read it, and approve it. Only a person can.")
    say(f"  {step + 2}. Start Claude Code here again, and run: /company <a command>")
    return 0
