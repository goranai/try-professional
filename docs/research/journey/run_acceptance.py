"""The acceptance journey, the way a Claude Code user would run it. Uses your Claude plan, not an API key.

usage: python run_acceptance.py <output_dir>

One prompt to a company, one command to a result, and a second related command later. It uses what `trypro agents
claude-code` generates: one Claude Code session as the operator, the /company command, and a real subagent for each role.

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
_LIMIT = re.compile(r"usage limit|rate.?limit|limit reached|hit your limit|too many requests|overloaded", re.IGNORECASE)
_DESIGN_PROMPT = (
    f"Use the trypro tools to design a company for this business: {DESCRIPTION}. Call org_propose, and do what each "
    "answer asks: submit the design with org_proposal_submit, and then judge each role's skills with "
    "org_skill_judgment_submit. When the status is ready_for_a_person, reply with only the proposal id."
)


ROLE_PREFIX = f"mcp__{ROLE_SERVER}__"


def session(prompt: str, cwd: Path, config: Path, tools: str, allowed: str) -> dict[str, Any]:
    """One Claude Code session, as a user would run it. It waits out a usage limit."""
    command = [
        _claude(), "-p", prompt,
        "--mcp-config", str(config),
        "--strict-mcp-config",
        "--tools", tools,
        "--allowedTools", allowed,
        "--model", OPERATOR_MODEL,
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
    usage = result.get("usage") or {}
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
        "usage": {
            "input": usage.get("input_tokens", 0),
            "cache_write": usage.get("cache_creation_input_tokens", 0),
            "cache_read": usage.get("cache_read_input_tokens", 0),
            "output": usage.get("output_tokens", 0),
            "cost_usd_at_list_price": result.get("total_cost_usd"),
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
        progress["design"] = session(_DESIGN_PROMPT, root, config, "", "mcp__trypro__*")
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
        progress[label] = session(f"/company {goal}", root, root / ".mcp.json", "Agent", allowed)
        progress[label]["minutes"] = round((time.monotonic() - started) / 60, 1)
        save()
        print(f"[{label}] {str(progress[label]['result'])[:600]}", flush=True)
        print(f"[{label}] usage {progress[label]['usage']}", flush=True)
    progress["attestation"] = attestation(root)
    save()
    print(json.dumps(progress["attestation"], ensure_ascii=False, indent=1)[:3000], flush=True)
    problems = [run for run, row in progress["attestation"].items() if row["claimed"] or not row["attested"]]
    problems += [f"{label} left its server" for label in ("first", "second") if progress[label].get("role_agents_outside_their_server")]
    if problems:
        raise SystemExit(f"The hierarchy wasn't real in: {problems}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main(Path(sys.argv[1]).resolve())
