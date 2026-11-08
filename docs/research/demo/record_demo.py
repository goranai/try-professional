"""Record the README's demo from a real run. Uses your Claude plan, not an API key.

usage: python record_demo.py <folder> <demo.json> [--command-only]

It runs what the quickstart says, in a new folder: `trypro setup claude-code`, Claude Code designing a company, the
review screen, one /company command, and the deliverables. Each step's real output and duration go to demo.json, and
render_demo.py draws it.

Two things differ from a person at a keyboard, and the README says so. Claude Code runs non-interactively, with `-p`.
And a script answers the real review screen, standing in for the person who approves: it runs `trypro init --review`
with the terminal check switched off, and types "a" and "approve". Nothing else is changed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

BUSINESS = "a catering business that delivers office lunches"
DESIGN = f"Use the trypro tools to design a company for {BUSINESS}."
COMMAND = (
    "Reply to a customer who asked whether we cover vegetarian meals. We do: every day's menu has a vegetarian "
    "main. Keep it to two sentences."
)
_APPROVE = """
import builtins, sys
from try_professional import cli
cli._person_at_terminal = lambda: True
answers = iter(["a", "approve"])
def typed(prompt=""):
    answer = next(answers)
    print(prompt + answer)
    return answer
builtins.input = typed
sys.exit(cli.main(["init", "--review", sys.argv[1]]))
"""


def _claude() -> str:
    found = shutil.which("claude") or shutil.which("claude.cmd")
    if not found:
        raise SystemExit("Claude Code isn't on the PATH.")
    if found.lower().endswith(".cmd"):
        # On Windows the .cmd wrapper cuts an argument at a newline. Run the program itself.
        return str(Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe")
    return found


def run(command: list[str], cwd: Path, stdin: str | None = None) -> tuple[str, float]:
    started = time.monotonic()
    done = subprocess.run(
        command,
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "MCP_TIMEOUT": "120000", "MCP_TOOL_TIMEOUT": "900000"},
        timeout=3600,
    )
    return (done.stdout + done.stderr).strip(), round(time.monotonic() - started, 1)


def claude(prompt: str, cwd: Path, allowed: str) -> tuple[dict[str, Any], float]:
    command = [_claude(), "-p", prompt, "--allowedTools", allowed, "--model", "sonnet", "--output-format", "json"]
    output, seconds = run(command, cwd, stdin="")
    return json.loads(output[output.index("{") :]), seconds


def command_only(folder: Path, out: Path) -> None:
    """Run the /company step again in a company the full run made, and replace it and what follows in demo.json."""
    demo = json.loads(out.read_text(encoding="utf-8"))
    steps = [s for s in demo["steps"] if not s.get("in_claude_code") and not s["typed"].startswith("cat ")]
    result, seconds = claude(f"/company {COMMAND}", folder, "Agent,mcp__trypro__*,mcp__trypro_role__*")
    steps.append({"typed": f"/company {COMMAND}", "output": result.get("result", ""), "seconds": seconds,
                  "usage": result.get("modelUsage"), "in_claude_code": True})  # fmt: skip
    indexes = sorted((folder / "deliverables").glob("run-*/index.md"), key=lambda p: p.stat().st_mtime)
    if indexes:
        relative = indexes[-1].relative_to(folder).as_posix()
        steps.append({"typed": f"cat {relative}", "output": indexes[-1].read_text(encoding="utf-8"), "seconds": 0})
    text = json.dumps({**demo, "steps": steps}, ensure_ascii=False, indent=1)
    for private in {str(folder), str(folder).replace("\\", "/"), str(Path.home()), str(Path.home()).replace("\\", "/")}:
        text = text.replace(private.replace("\\", "\\\\"), "<folder>").replace(private, "<folder>")
    out.write_text(text, encoding="utf-8")


def main(folder: Path, out: Path) -> None:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        raise SystemExit("An Anthropic key is set, which would bill the API. Unset it, so this uses your Claude plan.")
    if folder.exists():
        raise SystemExit(f"{folder} exists. The demo starts from an empty folder.")
    folder.mkdir(parents=True)
    trypro = shutil.which("trypro")
    if not trypro:
        raise SystemExit("trypro isn't on the PATH.")
    steps: list[dict[str, Any]] = []
    try:
        output, seconds = run([trypro, "setup", "claude-code"], folder)
        steps.append({"typed": "trypro setup claude-code", "output": output, "seconds": seconds})

        result, seconds = claude(DESIGN, folder, "mcp__trypro__*")
        steps.append({"typed": f'claude "{DESIGN}"', "output": result.get("result", ""), "seconds": seconds,
                      "usage": result.get("modelUsage")})  # fmt: skip
        proposal = re.search(r"prop-[0-9a-f]+", result.get("result", ""))
        if proposal is None:
            raise SystemExit(f"No proposal came back: {str(result)[:400]}")

        output, seconds = run([sys.executable, "-c", _APPROVE, proposal.group()], folder)
        steps.append({"typed": f"trypro init --review {proposal.group()}", "output": output, "seconds": seconds})

        result, seconds = claude(f"/company {COMMAND}", folder, "Agent,mcp__trypro__*,mcp__trypro_role__*")
        steps.append({"typed": f"/company {COMMAND}", "output": result.get("result", ""), "seconds": seconds,
                      "usage": result.get("modelUsage"), "in_claude_code": True})  # fmt: skip

        indexes = sorted((folder / "deliverables").glob("*/index.md"))
        if indexes:
            relative = indexes[-1].relative_to(folder).as_posix()
            steps.append({"typed": f"cat {relative}", "output": indexes[-1].read_text(encoding="utf-8"), "seconds": 0})
    finally:
        # Take the server back out of Claude Code's settings for this folder.
        run([_claude(), "mcp", "remove", "trypro"], folder)
        out.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps({"business": BUSINESS, "steps": steps}, ensure_ascii=False, indent=1)
        for private in {
            str(folder),
            str(folder).replace("\\", "/"),
            str(Path.home()),
            str(Path.home()).replace("\\", "/"),
        }:
            text = text.replace(private.replace("\\", "\\\\"), "<folder>").replace(private, "<folder>")
        out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    if "--command-only" in sys.argv:
        command_only(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
    else:
        main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
