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
    root: Path,
    say: Callable[[str], None],
    add: Callable[[Path], tuple[bool, str]] = add_server,
    next_steps: bool = True,
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
    if not next_steps:
        return 0
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


# ---- trypro start ------------------------------------------------------------------------------------------------

DESIGN_ROUNDS = 4
_DESIGN = (
    "Use the trypro tools to design a company that can do this, for the business it's for: {request}\n\n"
    "Call org_propose with a short description of that business, and do what each answer asks: submit the design "
    "with org_proposal_submit, and judge each role's skills with org_skill_judgment_submit. When the status is "
    "ready_for_a_person, reply with only the proposal id."
)
_CONTINUE = (
    "Use the trypro tools to finish designing proposal {proposal}. Call org_proposal_get with it, and do what each "
    "answer asks, until the status is ready_for_a_person. Then reply with only the proposal id."
)


def borrow_github_token(say: Callable[[str], None]) -> None:
    """Skills need a GitHub token. If none is set, the GitHub CLI's is used for this command only. It's never saved
    or printed."""
    if github_token_is_set():
        return
    gh = shutil.which("gh")
    if gh is None:
        return
    token = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=_TIMEOUT_SECONDS).stdout
    if token.strip():
        import os

        os.environ["GITHUB_TOKEN"] = token.strip()
        say("[ok] Using your GitHub CLI's token for skills, for this command only")


def ask_claude(root: Path, prompt: str) -> str:
    """One headless Claude Code call in this folder, with only the trypro tools. On the person's own plan."""
    claude = shutil.which("claude")
    if claude is None:
        raise RuntimeError("Claude Code isn't on the PATH.")
    done = subprocess.run(
        [claude, "-p", prompt, "--tools", "", "--allowedTools", f"mcp__{SERVER_NAME}__*", "--output-format", "text"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=3600,
    )
    return (done.stdout or "") + (done.stderr or "")


def proposal_status(root: Path, proposal: str) -> str:
    try:
        meta = json.loads((root / ".trypro" / "proposals" / proposal / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "missing"
    return str(meta.get("status", "missing"))


def design_with_claude(
    root: Path, request: str, say: Callable[[str], None], ask: Callable[[Path, str], str] = ask_claude
) -> str | None:
    """Claude Code designs a company for the request, and keeps going until it's ready for a person. Returns its id."""
    import re

    say("Designing a company for this. It takes a few minutes, on your Claude plan.")
    answer = ask(root, _DESIGN.format(request=request))
    found = re.search(r"prop-[0-9a-f]+", answer)
    if found is None:
        say(f"Claude Code didn't design a company. What it said: {answer.strip()[:400]}")
        return None
    proposal = found.group()
    for _ in range(DESIGN_ROUNDS):
        if proposal_status(root, proposal) in ("proposed", "approved"):
            return proposal
        say("The design isn't finished yet, so Claude Code carries on with it.")
        ask(root, _CONTINUE.format(proposal=proposal))
    if proposal_status(root, proposal) in ("proposed", "approved"):
        return proposal
    say(f"The design didn't finish. To try again: trypro start, or trypro init --review {proposal}")
    return None


def launch_claude(root: Path, request: str) -> int:
    """Open Claude Code here, on the request. Interactive, so it can ask a person to trust this folder."""
    claude = shutil.which("claude")
    if claude is None:
        return 2
    return subprocess.call([claude, f"/company {request}"], cwd=root)
