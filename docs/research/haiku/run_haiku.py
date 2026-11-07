"""Run the acceptance journey on Haiku, and check what 0.1.1 changed. Uses your Claude plan, not an API key.

usage: python run_haiku.py <output_dir>

Every Claude Code process here runs `haiku`: the session that coordinates, and every role's subagent, written with
`trypro agents claude-code --model haiku`. It records how Haiku does, and checks four things 0.1.1 changed:

1. Designing with no GITHUB_TOKEN: the session should pass on how to set one.
2. A stopped command: the session should give the person the exact `trypro decide` and `/company continue` lines.
3. `/company continue <run-id>`: the session should pick that run up, and start nothing new.
4. The /company wording behind both.

To make a command stop, it lowers that company's small-command limit before the third command, as a person could in
org.yaml. A script stands in for the person who approves the company and answers the stop, and the record says so.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "journey"))

from run_acceptance import (  # noqa: E402
    _DESIGN_PROMPT,
    COORDINATING,
    DESCRIPTION,
    FIRST,
    ROLE_PREFIX,
    SECOND,
    attestation,
    session,
)

from try_professional.adapters.mcp.claude_code import server_config, write_claude_code_files  # noqa: E402
from try_professional.cli import _seed_company_memory, _write_project_files  # noqa: E402
from try_professional.config import load_config  # noqa: E402
from try_professional.core.orchestrator import Orchestrator  # noqa: E402
from try_professional.core.pipeline import HumanDecided  # noqa: E402
from try_professional.generator import activate, load_proposal  # noqa: E402
from try_professional.generator.review import render  # noqa: E402
from try_professional.memory import FastEmbedEmbedder  # noqa: E402
from try_professional.memory.embedding import use_embedding_service  # noqa: E402
from try_professional.org import load_org  # noqa: E402

MODEL = "haiku"
THIRD = (
    "Write a short FAQ, of five questions and answers, for the bakery's pre-order page, following the homepage copy."
)
LOW_LIMIT = 8_000
DEFAULT_LIMIT = 60_000
NATURAL_DESIGN = f"Use the trypro tools to design a company for {DESCRIPTION}."
BY = "the Haiku check harness, standing in for a person"
_RUN = re.compile(r"run-[0-9a-f]{12}")


def events(stream: Path) -> list[dict[str, Any]]:
    """The session's own steps, in order: what it said, and which tools it called with what."""
    found: list[dict[str, Any]] = []
    for line in stream.read_text(encoding="utf-8").splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("type") != "assistant" or message.get("parent_tool_use_id"):
            continue
        for block in (message.get("message") or {}).get("content") or []:
            if block.get("type") == "text":
                found.append({"said": block.get("text", "")})
            elif block.get("type") == "tool_use":
                found.append({"tool": block.get("name"), "input": block.get("input")})
    return found


def shells_and_skills(found: list[dict[str, Any]]) -> list[str]:
    """Anything the session did besides coordinating. Loading /company itself, as a skill, is coordinating."""
    odd = []
    for item in found:
        tool = item.get("tool")
        if tool is None or tool in COORDINATING:
            continue
        if tool == "Skill" and (item.get("input") or {}).get("skill") == "company":
            continue
        odd.append(f"{tool} {json.dumps(item.get('input'), ensure_ascii=False)[:120]}")
    return odd


def run_company(goal: str, root: Path, output: Path, label: str) -> dict[str, Any]:
    started = time.monotonic()
    allowed = f"Agent,mcp__trypro__*,{ROLE_PREFIX}*"
    stream = output / f"{label}.stream.jsonl"
    summary = session(f"/company {goal}", root, [], allowed, MODEL, stream)
    summary["minutes"] = round((time.monotonic() - started) / 60, 1)
    summary["session_other_calls"] = shells_and_skills(events(stream))
    print(f"[{label}] {str(summary['result'])[:300]}", flush=True)
    return summary


def set_small_limit(root: Path, limit: int) -> None:
    path = root / "org" / "org.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["pipeline"]["budget"]["max_tokens_per_small_command"] = limit
    path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")


def check_token_guidance(output: Path, progress: dict[str, Any]) -> None:
    """Design with no token. The guidance should reach the person, and before the design work if it can."""
    folder = output / "no-token"
    folder.mkdir(parents=True, exist_ok=True)
    config = output / "no-token.json"
    config.write_text(json.dumps({"mcpServers": {"trypro": server_config(folder)}}), encoding="utf-8")
    saved = os.environ.pop("GITHUB_TOKEN", None)
    try:
        stream = output / "no-token.stream.jsonl"
        flags = ["--mcp-config", str(config), "--strict-mcp-config", "--tools", ""]
        summary = session(NATURAL_DESIGN, folder, flags, "mcp__trypro__*", MODEL, stream)
    finally:
        if saved is not None:
            os.environ["GITHUB_TOKEN"] = saved
    found = events(stream)
    said = [i["said"] for i in found if "said" in i] + [str(summary.get("result", ""))]
    submitted_at = next((n for n, i in enumerate(found) if i.get("tool") == "mcp__trypro__org_proposal_submit"), None)
    told_at = next((n for n, i in enumerate(found) if "GITHUB_TOKEN" in i.get("said", "")), None)
    progress["no_token"] = {
        **summary,
        "told_the_person": any("GITHUB_TOKEN" in text for text in said),
        "gave_the_command": any("gh auth token" in text for text in said),
        "told_before_designing": told_at is not None and (submitted_at is None or told_at < submitted_at),
        "went_on_to_design": submitted_at is not None,
    }
    shown = ("told_the_person", "gave_the_command", "told_before_designing", "went_on_to_design")
    print(f"[no-token] {json.dumps({k: progress['no_token'][k] for k in shown})}", flush=True)


def main(output: Path) -> None:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        raise SystemExit("An Anthropic key is set, which would bill the API. Unset it, so this uses your Claude plan.")
    if not os.environ.get("GITHUB_TOKEN"):
        raise SystemExit("Set GITHUB_TOKEN. The main company is designed with one, and the first check runs without.")
    output.mkdir(parents=True, exist_ok=True)
    root = output / "company"
    root.mkdir(exist_ok=True)
    progress_path = output / "progress.json"
    progress: dict[str, Any] = json.loads(progress_path.read_text("utf-8")) if progress_path.is_file() else {}

    def save() -> None:
        progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding="utf-8")

    if "no_token" not in progress:
        check_token_guidance(output, progress)
        save()
    if "design" not in progress:
        config = output / "operator.json"
        config.write_text(json.dumps({"mcpServers": {"trypro": server_config(root)}}), encoding="utf-8")
        flags = ["--mcp-config", str(config), "--strict-mcp-config", "--tools", ""]
        print("Designing the company, on Haiku", flush=True)
        stream = output / "design.stream.jsonl"
        progress["design"] = session(_DESIGN_PROMPT, root, flags, "mcp__trypro__*", MODEL, stream)
        save()
    if "activated" not in progress:
        proposal_id = re.search(r"prop-[0-9a-f]+", str(progress["design"]["result"]))
        if proposal_id is None:
            raise SystemExit(f"No proposal came back: {str(progress['design'])[:500]}")
        bundle = load_proposal(root, proposal_id.group())
        use_embedding_service(root / ".trypro")
        _write_project_files(root)
        activate(bundle, root, FastEmbedEmbedder(), by=BY)
        _seed_company_memory(bundle, root, BY)
        write_claude_code_files(root, load_org(root / "org"), MODEL)
        progress["activated"] = proposal_id.group()
        screen = render(bundle).splitlines()
        progress["review_screen_coverage"] = next((line for line in screen if "Skill coverage" in line), None)
        save()
    for label, goal in (("first", FIRST), ("second", SECOND)):
        if label not in progress:
            progress[label] = run_company(goal, root, output, label)
            save()
    if "stopped" not in progress:
        set_small_limit(root, LOW_LIMIT)
        progress["stopped"] = run_company(THIRD, root, output, "stopped")
        save()
    if "answered" not in progress:
        run_id = _RUN.findall(str(progress["stopped"]["result"]))
        progress["stopped"]["run_named"] = run_id[0] if run_id else None
        stopped = [r for r, row in attestation(root).items() if row["goal"] == THIRD and row["phase"] == "blocked"]
        if not stopped:
            raise SystemExit("The third command didn't stop, so the stop can't be checked.")
        run = stopped[0]
        result = str(progress["stopped"]["result"])
        progress["stopped"]["checks"] = {
            "gave_the_decide_lines": f"trypro decide {run} " in result,
            "gave_the_continue_line": f"/company continue {run}" in result,
            "said_it_stopped_at_the_limit": "limit" in result.lower(),
        }
        set_small_limit(root, DEFAULT_LIMIT)
        config = load_config(root)
        orchestrator = Orchestrator(load_org(config.org_dir), config.home)
        try:
            request = next(r for r in orchestrator.state(run).human_requests.values() if r.kind == "budget")
            orchestrator.handle(run, HumanDecided(request_id=request.id, decision="resume", note=BY))
            progress["answered"] = {"run": run, "budget_override": orchestrator.state(run).budget_override}
        finally:
            orchestrator.close()
        save()
    if "continued" not in progress:
        run = progress["answered"]["run"]
        progress["continued"] = run_company(f"continue {run}", root, output, "continued")
        found = events(output / "continued.stream.jsonl")
        progress["continued"]["started_a_new_run"] = any(i.get("tool") == "mcp__trypro__run_start" for i in found)
        save()
    progress["attestation"] = attestation(root)
    save()
    problems = []
    for run, row in progress["attestation"].items():
        if row["phase"] != "done":
            # Not a fault of 0.1.1 by itself. Which ones are, is read from the record.
            progress.setdefault("unfinished", []).append(f"{run} ({row['goal'][:40]}) ended at {row['phase']}")
        if row["claimed"]:
            problems.append(f"{run} has {row['claimed']} artifacts recorded as claimed")
    for label in ("first", "second", "stopped", "continued"):
        if progress[label].get("role_agents_outside_their_server"):
            problems.append(f"a role in {label} left its server")
        if progress[label].get("session_other_calls"):
            problems.append(f"the session in {label} also called {progress[label]['session_other_calls']}")
    if len(progress["attestation"]) != 3:
        problems.append(f"there are {len(progress['attestation'])} runs, not 3: /company continue started a new one")
    if not progress["continued"]["result"] or progress["answered"]["run"] not in str(progress["continued"]["result"]):
        problems.append("/company continue didn't report on the run it was given")
    for name, passed in progress["stopped"]["checks"].items():
        if not passed:
            problems.append(f"stopped command: {name} failed")
    if not progress["no_token"]["told_the_person"]:
        problems.append("no-token: the session didn't pass the guidance on")
    progress["problems"] = problems
    save()
    print(json.dumps({"problems": problems}, ensure_ascii=False, indent=1), flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
