"""The acceptance journey, the way a Claude Code user would run it. Uses your Claude plan, not an API key.

usage: python run_acceptance.py <output_dir>

One prompt to a company, one command to a result, and a second related command later. It uses what `trypro agents
claude-code` generates: one Claude Code session that only coordinates, the /company command, and a real subagent for
each role.

This reads every tool call in the session's stream, and reports any role subagent that called anything but its own
bound server, and fails if any work was recorded as claimed, because only a bound server can record it as attested.
An earlier version of the generated files let one subagent do every role's work. This is the check that caught it.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from run_claude_code import WAIT_FOR_LIMIT_SECONDS, _claude

from try_professional.adapters.mcp.claude_code import ROLE_SERVER, server_config, write_claude_code_files
from try_professional.cli import _seed_company_memory, _write_project_files
from try_professional.generator import activate, load_proposal
from try_professional.memory import FastEmbedEmbedder
from try_professional.memory.embedding import use_embedding_service
from try_professional.org import load_org

DESCRIPTION = "a web design agency for small local businesses"
FIRST = "Write a one-page website proposal for a family bakery that wants online pre-orders."
SECOND = "The bakery liked the proposal. Write the homepage copy for the site we proposed, following that proposal."
OPERATOR_MODEL = "sonnet"
SESSION_TIMEOUT_SECONDS = 3600
_LIMIT = re.compile(r"usage limit|rate.?limit|limit reached|hit your (\S+ )?limit|resets? \d|too many requests|overloaded", re.IGNORECASE)
_DESIGN_PROMPT = (
    f"Use the trypro tools to design a company for this business: {DESCRIPTION}. Call org_propose, and do what each "
    "answer asks: submit the design with org_proposal_submit, and then judge each role's skills with "
    "org_skill_judgment_submit. When the status is ready_for_a_person, reply with only the proposal id."
)


ROLE_PREFIX = f"mcp__{ROLE_SERVER}__"
# ToolSearch is how Claude Code loads a tool's definition. It reads nothing of the company's, and can't do work.
COORDINATING = (
    "Agent",
    "Task",
    "ToolSearch",
    "mcp__trypro__run_start",
    "mcp__trypro__run_next_action",
    "mcp__trypro__run_status",
)


def session(
    prompt: str, cwd: Path, flags: list[str], allowed: str, model: str = OPERATOR_MODEL, save: Path | None = None
) -> dict[str, Any]:
    """One Claude Code session. It waits out a usage limit. `save` keeps its whole stream, to read in order later."""
    command = [
        _claude(), "-p", prompt,
        *flags,
        "--allowedTools", allowed,
        "--model", model,
        "--output-format", "stream-json",
        "--verbose",
        "--no-session-persistence",
    ]  # fmt: skip
    environment = {**os.environ, "MCP_TOOL_TIMEOUT": "900000", "MCP_TIMEOUT": "120000"}
    for name in ("TRYPRO_LLM_API_KEY", "TRYPRO_LLM_BASE_URL", "TRYPRO_LLM_MODEL"):
        environment.pop(name, None)
    while True:
        done = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=SESSION_TIMEOUT_SECONDS,
        )
        summary = read_stream(done.stdout)
        if save is not None:
            save.write_text(done.stdout, encoding="utf-8")
        message = str(summary.get("result", ""))
        if not (summary.get("is_error") and _LIMIT.search(message)):
            return summary
        print(f"A usage limit was hit, so waiting {WAIT_FOR_LIMIT_SECONDS}s: {message[:120]}", flush=True)
        time.sleep(WAIT_FOR_LIMIT_SECONDS)


def read_stream(stdout: str) -> dict[str, Any]:
    """What the session did: who called which tool, and what it spent."""
    agents: dict[str, str] = {}
    calls_by_agent: dict[str, dict[str, int]] = {}
    operator_tools: dict[str, int] = {}
    result: dict[str, Any] = {}
    init_tools: list[str] = []
    for line in stdout.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("type") == "system" and message.get("subtype") == "init":
            init_tools = list(message.get("tools") or [])
        if message.get("type") == "result":
            result = message
        if message.get("type") != "assistant":
            continue
        parent = message.get("parent_tool_use_id")
        for block in (message.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = str(block.get("name"))
            if parent is None:
                operator_tools[name] = operator_tools.get(name, 0) + 1
                if name in ("Agent", "Task"):
                    agents[str(block.get("id"))] = str((block.get("input") or {}).get("subagent_type"))
            else:
                calls = calls_by_agent.setdefault(agents.get(str(parent), str(parent)), {})
                calls[name] = calls.get(name, 0) + 1
    # A result's "usage" is the session alone. Its subagents, which do the work, are only in modelUsage.
    by_model = result.get("modelUsage") or {}
    outside = {
        agent: [name for name in calls if not name.startswith(ROLE_PREFIX)]
        for agent, calls in calls_by_agent.items()
        if any(not name.startswith(ROLE_PREFIX) for name in calls)
    }
    return {
        "result": result.get("result"),
        "is_error": result.get("is_error"),
        "operator_could_use": init_tools,
        "operator_called": operator_tools,
        "role_agents_called": calls_by_agent,
        "role_agents_outside_their_server": outside,
        "session_did_more_than_coordinate": [n for n in operator_tools if n not in COORDINATING],
        "usage": {
            "input": sum(m.get("inputTokens", 0) for m in by_model.values()),
            "cache_write": sum(m.get("cacheCreationInputTokens", 0) for m in by_model.values()),
            "cache_read": sum(m.get("cacheReadInputTokens", 0) for m in by_model.values()),
            "output": sum(m.get("outputTokens", 0) for m in by_model.values()),
            "cost_usd_at_list_price": result.get("total_cost_usd"),
        },
        "usage_by_model": {
            name: {
                k: m.get(k, 0)
                for k in ("inputTokens", "cacheCreationInputTokens", "cacheReadInputTokens", "outputTokens")
            }
            for name, m in by_model.items()
        },
        "subagents": result.get("subagent_stats"),
        "turns": result.get("num_turns"),
    }


def attestation(root: Path) -> dict[str, Any]:
    """Who the pipeline recorded as doing the work. Only a bound server can record 'runtime'."""
    connection = sqlite3.connect(f"file:{root / '.trypro' / 'ledger.sqlite'}?mode=ro", uri=True)
    runs = {}
    for run_id, raw in connection.execute("SELECT run_id, state_json FROM runs"):
        state = json.loads(raw)
        made = [a for a in state["artifacts"].values() if a["produced_by"] != "orchestrator"]
        runs[run_id] = {
            "goal": state["goal"],
            "phase": state["phase"],
            "size": state["size"],
            "counters": state["counters"],
            "attested": sum(1 for a in made if a["attested_by"] == "runtime"),
            "claimed": sum(1 for a in made if a["attested_by"] == "claimed"),
            "by_role": sorted({a["produced_by"] for a in made}),
        }
    return runs


def main(output: Path) -> None:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        raise SystemExit("An Anthropic key is set, which would bill the API. Unset it, so this uses your Claude plan.")
    root = output / "company"
    root.mkdir(parents=True, exist_ok=True)
    progress_path = output / "progress.json"
    progress: dict[str, Any] = json.loads(progress_path.read_text("utf-8")) if progress_path.is_file() else {}

    def save() -> None:
        progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding="utf-8")

    config = output / "operator.json"
    config.write_text(json.dumps({"mcpServers": {"trypro": server_config(root)}}), encoding="utf-8")
    if "design" not in progress:
        print("Designing the company from one prompt", flush=True)
        # No company files exist yet, so this one has only the server it's given.
        flags = ["--mcp-config", str(config), "--strict-mcp-config", "--tools", ""]
        progress["design"] = session(_DESIGN_PROMPT, root, flags, "mcp__trypro__*")
        save()
    if "activated" not in progress:
        proposal_id = re.search(r"prop-[0-9a-f]+", str(progress["design"]["result"]))
        if proposal_id is None:
            raise SystemExit(f"No proposal came back: {str(progress['design'])[:500]}")
        bundle = load_proposal(root, proposal_id.group())
        by = "the journey harness, standing in for a person"
        use_embedding_service(root / ".trypro")
        _write_project_files(root)
        activate(bundle, root, FastEmbedEmbedder(), by=by)
        _seed_company_memory(bundle, root, by)
        write_claude_code_files(root, load_org(root / "org"))
        progress["activated"] = proposal_id.group()
        save()
    for label, goal in (("first", FIRST), ("second", SECOND)):
        if label in progress:
            continue
        print(f"[{label}] {goal}", flush=True)
        started = time.monotonic()
        allowed = f"Agent,mcp__trypro__*,{ROLE_PREFIX}*"
        # Only what a user has: the folder's own .mcp.json and agents, and every built-in tool. --strict-mcp-config
        # would stop each subagent's own server from connecting, and it isn't what a user runs.
        progress[label] = session(f"/company {goal}", root, [], allowed)
        progress[label]["minutes"] = round((time.monotonic() - started) / 60, 1)
        save()
        print(f"[{label}] {str(progress[label]['result'])[:600]}", flush=True)
        print(f"[{label}] usage {progress[label]['usage']}", flush=True)
    progress["attestation"] = attestation(root)
    save()
    print(json.dumps(progress["attestation"], ensure_ascii=False, indent=1)[:3000], flush=True)
    problems = []
    for run, row in progress["attestation"].items():
        if row["phase"] != "done":
            problems.append(f"{run} stopped at {row['phase']}")
        if row["claimed"]:
            problems.append(f"{run} has {row['claimed']} artifacts recorded as claimed, so its hierarchy wasn't real")
    for label in ("first", "second"):
        if progress[label].get("role_agents_outside_their_server"):
            problems.append(f"a role in {label} left its server")
        if progress[label].get("session_did_more_than_coordinate"):
            problems.append(f"the session in {label} used {progress[label]['session_did_more_than_coordinate']}")
    if problems:
        raise SystemExit(f"Acceptance failed: {problems}")
    print("Acceptance passed.", flush=True)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main(Path(sys.argv[1]).resolve())
