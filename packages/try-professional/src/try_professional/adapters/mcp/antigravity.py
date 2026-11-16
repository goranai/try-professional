"""Set a company up for Antigravity: its front desk, and a coordinator's server that runs each role's step in `agy -p`.

Antigravity reads a folder's rules (.agents/rules) and MCP servers (.agents/mcp_config.json), so the folder itself is
the company's front desk, as CLAUDE.md is for Claude Code. Each role's step runs in its own `agy -p`, started by the
coordinator's run_step, with only that role's server: Antigravity has no subagent bound to its own server. With its
permissions skipped, Antigravity asks the person nothing, so the coordinator has no tool that records a person's
decision there: the person makes each one at a terminal, with trypro.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ... import __version__
from ...org import Org
from .claude_code import SERVER, server_config
from .front_desk import MANAGED_END, YAML_START, front_desk_rules, write_managed

FOLDER = ".agents"
RULE = "trypro-front-desk.md"
VERSION_STAMP = "trypro-version"
CLIENT = "antigravity"

_RULE_HEADER = """trigger: always_on
description: This folder is a company run by Try Professional, and you're its front desk.
---

"""
_LEGACY_END = "waiting for me."

_COMMAND = """---
description: Give the company a command, and run it to a result.
---

Run what I wrote after /company through the company, as the front desk's rules say under "Running a command". If I \
wrote "continue" and a run_id, carry that run on instead.
"""


def write_antigravity_files(root: Path, org: Org) -> list[Path]:
    """The front desk's rule, the /company workflow, and the coordinator's server in .agents/mcp_config.json. Anything
    else already there is kept. Run it again after the company changes."""
    folder = root / FOLDER
    written = []
    rules = folder / "rules"
    rules.mkdir(parents=True, exist_ok=True)
    rule = rules / RULE
    old = rule.read_text(encoding="utf-8") if rule.is_file() else ""
    legacy = ""
    if old.startswith("---\ntrigger: always_on") and _LEGACY_END in old:
        legacy = old[: old.index(_LEGACY_END) + len(_LEGACY_END)]
    write_managed(rule, _RULE_HEADER + front_desk_rules(org, CLIENT), start=YAML_START, end=MANAGED_END, legacy=legacy)
    written.append(rule)
    workflows = folder / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    command = workflows / "company.md"
    write_managed(command, _COMMAND.removeprefix("---\n"), start=YAML_START, end=MANAGED_END, legacy=_COMMAND.rstrip())
    written.append(command)
    mcp = folder / "mcp_config.json"
    config: dict[str, Any] = json.loads(mcp.read_text(encoding="utf-8")) if mcp.is_file() else {}
    server = {k: v for k, v in server_config(root).items() if k != "type"}
    server["args"] = [*server["args"], "--client", CLIENT]
    config.setdefault("mcpServers", {})[SERVER] = server
    mcp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8", newline="\n")
    written.append(mcp)
    stamp = folder / VERSION_STAMP
    stamp.write_text(f"{__version__}\n", encoding="utf-8", newline="\n")
    written.append(stamp)
    return written


def refresh_antigravity_files(root: Path, org: Org) -> bool:
    """Rewrite them if a different version of trypro wrote them. A folder never set up for Antigravity is left alone."""
    stamp = root / FOLDER / VERSION_STAMP
    if not (root / FOLDER / "rules" / RULE).is_file():
        return False
    if stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == __version__:
        return False
    try:
        write_antigravity_files(root, org)
    except OSError:
        return False
    return True
