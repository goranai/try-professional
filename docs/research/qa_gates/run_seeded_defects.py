"""Do the reviewers catch flawed work? Uses your Claude plan, not an API key.

usage: python run_seeded_defects.py <companies_dir> <output_dir> <results.json> [first|harder|retest]

Each trial is a real run over real MCP, on a fresh copy of a real company, so no reviewer learns from an earlier trial.
The CEO's intake and every review are real, each its own `claude -p` process with only that role's server. The one thing
that isn't real is the work: when the pipeline asks for it, this submits a prepared document through the owner's own
bound server, reporting every criterion as met. So a reviewer gets what a careless or dishonest worker would send.

A trial ends at the first rejection, or once every gate has passed. It counts as caught only if a reviewer rejected the
work, and a major or blocking issue was about the seeded flaw. A clean document goes through too, and a rejection of
that is a false alarm.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters
from scenarios import CLEAN, DEFECTS, FIRST_SET, HARDER_SET, UNSEEN_SET, Scenario, check_first, check_harder

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "journey"))

from run_claude_code import (  # noqa: E402
    _ROLE_SYSTEM,
    TIER_MODELS,
    Operator,
    _add_usage,
    _empty_usage,
    run_claude,
)

from try_professional.adapters.mcp.claude_code import server_config  # noqa: E402
from try_professional.memory.embed_service import ADDRESS_FILE, _connect_or_none, _read_address, _start  # noqa: E402
from try_professional.org import load_org  # noqa: E402

MAX_STEPS = 12
MAX_TRIES = 3
FRESH_IGNORES = shutil.ignore_patterns(ADDRESS_FILE, "embed-service.lock", "__pycache__")


def _state(root: Path, run_id: str) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{root / '.trypro' / 'ledger.sqlite'}?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT state_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    finally:
        connection.close()
    state: dict[str, Any] = json.loads(row[0])
    return state


def _payload(
    output_type: str, document: str, summary: str, evidence: str, criteria: list[str], locale: str
) -> dict[str, Any]:
    met = [{"criterion": c, "met": True, "evidence": evidence} for c in criteria]
    if output_type == "TaskReport":
        return {"summary": summary, "outputs": [document], "done_criteria_met": met, "open_issues": []}
    if output_type == "PublicContent":
        return {
            "audience": "the customer named in the command",
            "channel": "as the command says",
            "locale": locale,
            "body": document,
            "claims": [],
            "done_criteria_met": met,
        }
    raise ValueError(f"This test doesn't write a {output_type}")


def _verdicts(state: dict[str, Any], subject: str) -> list[dict[str, Any]]:
    return [
        {
            "gate": a["payload"]["gate"],
            "role": a["produced_by"],
            "attested_by": a["attested_by"],
            "decision": a["payload"]["decision"],
            "issues": a["payload"]["issues"],
        }
        for a in state["artifacts"].values()
        if a["type"] == "ReviewVerdict" and a["payload"].get("subject_artifact_id") == subject
    ]


def score(version: str, spot: str | None, verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    """What a reviewer did about this version. Pure, so the counting can be checked by reading it."""
    rejected = [v for v in verdicts if v["decision"] != "approve"]
    pattern = re.compile(spot, re.IGNORECASE) if spot else None

    def about_it(issue: dict[str, Any]) -> bool:
        return pattern is not None and pattern.search(issue["description"]) is not None

    serious = [i for v in rejected for i in v["issues"] if i["severity"] != "minor"]
    every = [i for v in verdicts for i in v["issues"]]
    return {
        "rejected": bool(rejected),
        "caught": any(about_it(i) for i in serious) if version != CLEAN else False,
        "noticed_but_approved": not rejected and any(about_it(i) for i in every),
        "false_alarm": version == CLEAN and bool(rejected),
        "first_rejecting_gate": rejected[0]["gate"] if rejected else None,
    }


def fresh_company(base: Path, target: Path, hub: Path) -> None:
    """A copy as the journey left it, using the one embedding service, so 25 trials don't start 25 of them."""
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(base, target, ignore=FRESH_IGNORES)
    if _connect_or_none(hub) is None:
        _start(hub, None)
    shutil.copyfile(hub / ADDRESS_FILE, target / ".trypro" / ADDRESS_FILE)


def _stop_own_service(root: Path, hub: Path) -> None:
    """If a trial's copy started its own service after all, stop it by its exact process id, which this started."""
    here, shared = _read_address(root / ".trypro"), _read_address(hub)
    if here and shared and here.get("pid") != shared.get("pid"):
        import psutil

        with contextlib.suppress(psutil.Error):
            psutil.Process(int(here["pid"])).terminate()


async def _submit_seeded(
    root: Path, action: dict[str, Any], run_id: str, payload: dict[str, Any], flags: dict[str, bool]
) -> Any:
    config = server_config(root, action["role"])
    parameters = StdioServerParameters(command=config["command"], args=config["args"], env=dict(os.environ))
    async with Client(parameters) as bound:
        return await Operator(bound).call(
            "artifact_submit", run_id=run_id, invocation_id=action["invocation_id"], payload=payload, flags=flags
        )


async def trial(scenario: Scenario, version: str, base: Path, folder: Path, hub: Path) -> dict[str, Any]:
    root = folder / "company"
    fresh_company(base, root, hub)
    org = load_org(root / "org")
    document, summary, evidence = scenario.version(version)
    usage = _empty_usage()
    started = time.monotonic()
    config = server_config(root)
    parameters = StdioServerParameters(command=config["command"], args=config["args"], env=dict(os.environ))
    row: dict[str, Any] = {"scenario": scenario.id, "business": scenario.business, "version": version}
    subject: str | None = None
    tries: dict[str, int] = {}
    async with Client(parameters) as client:
        operator = Operator(client)
        run_id = (await operator.call("run_start", goal=scenario.goal))["run_id"]
        row["run_id"] = run_id
        for _ in range(MAX_STEPS):
            now = await operator.call("run_next_action", run_id=run_id)
            if now["status"] != "working":
                row["ended"] = now["status"]
                row["reason"] = str(now.get("reason") or now.get("needs_a_person"))[:500]
                break
            if not now["actions"]:
                await asyncio.sleep(1)
                continue
            action = now["actions"][0]
            purpose = action["purpose"]
            print(f"    {purpose:<12} {action['role']}", flush=True)
            if purpose == "integrate":
                row["ended"] = "every_gate_passed"
                break
            state = _state(root, run_id)
            if purpose == "execute":
                if subject is not None:
                    # It came back to the worker. Check the reviewer's words reached them, and stop here.
                    opened = state["invocations"][action["invocation_id"]]
                    row["ended"] = "sent_back_to_the_worker"
                    row["feedback_reached_the_worker"] = bool(opened.get("feedback"))
                    row["feedback"] = opened.get("feedback")
                    break
                task_id = state["invocations"][action["invocation_id"]]["task_id"]
                real_tasks = [t for t in state["tasks"] if t != "__final__"]
                if len(real_tasks) != 1:
                    row["ended"] = "out_of_scope_more_than_one_task"
                    break
                brief = next(
                    a for a in state["artifacts"].values() if a["type"] == "TaskBrief" and a["task_id"] == task_id
                )
                row["definition_of_done"] = brief["payload"]["definition_of_done"]
                row["output_type"] = brief["payload"]["output_type"]
                row["owner"], row["reviewer"] = action["role"], brief["payload"]["reviewer_role"]
                if row["output_type"] not in ("TaskReport", "PublicContent"):
                    row["ended"] = "out_of_scope_output_type"
                    break
                payload = _payload(
                    brief["payload"]["output_type"],
                    document,
                    summary,
                    evidence,
                    brief["payload"]["definition_of_done"],
                    state.get("user_language") or "en",
                )
                answer = await _submit_seeded(root, action, run_id, payload, {"public_facing": True})
                if isinstance(answer, dict) and answer.get("accepted") is False:
                    row["ended"] = "the_seeded_work_was_refused_by_code"
                    row["reason"] = str(answer.get("reason"))[:500]
                    break
                state = _state(root, run_id)
                subject = state["tasks"][task_id]["result_artifact_id"]
                row["subject"] = subject
                row["seeded_attested_by"] = state["artifacts"][subject]["attested_by"]
                continue
            invocation = action["invocation_id"]
            tries[invocation] = tries.get(invocation, 0) + 1
            if tries[invocation] > MAX_TRIES:
                row["ended"] = f"stuck_at_{purpose}"
                break
            model = TIER_MODELS.get(org.role(action["role"]).model_tier, "sonnet")
            result = await asyncio.to_thread(
                run_claude,
                f"run_id: {run_id}\ninvocation_id: {invocation}",
                _ROLE_SYSTEM,
                server_config(root, action["role"]),
                model,
                "mcp__trypro__*",
            )
            _add_usage(usage, result)
        else:
            row["ended"] = "too_many_steps"
    state = _state(root, row["run_id"])
    row["goal_language"] = state.get("user_language")
    row["size"] = state.get("size")
    verdicts = _verdicts(state, subject) if subject else []
    row["verdicts"] = verdicts
    row["usage"] = usage
    row["minutes"] = round((time.monotonic() - started) / 60, 1)
    row.update(score(version, scenario.spot.get(version), verdicts))
    _stop_own_service(root, hub)
    return row


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counted = [r for r in rows if r.get("subject")]
    summary: dict[str, Any] = {
        "defects": {},
        "not_counted": [f"{r['scenario']}/{r['version']}: {r['ended']}" for r in rows if not r.get("subject")],
    }
    for defect in DEFECTS:
        these = [r for r in counted if r["version"] == defect]
        summary["defects"][defect] = {
            "trials": len(these),
            "caught": sum(r["caught"] for r in these),
            "rejected": sum(r["rejected"] for r in these),
            "noticed_but_approved": sum(r["noticed_but_approved"] for r in these),
        }
    clean = [r for r in counted if r["version"] == CLEAN]
    summary["clean"] = {"trials": len(clean), "false_alarms": sum(r["false_alarm"] for r in clean)}
    total = _empty_usage()
    for r in rows:
        for key in total:
            total[key] = round(total[key] + r["usage"][key], 5)
    summary["usage"] = total
    return summary


async def main(companies: Path, output: Path, results: Path, chosen: str) -> None:
    check_first()
    check_harder()
    scenarios = {"first": FIRST_SET, "harder": HARDER_SET, "retest": HARDER_SET + UNSEEN_SET}[chosen]
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        raise SystemExit("An Anthropic key is set, which would bill the API. Unset it, so this uses your Claude plan.")
    hub = output / "embedding-hub"
    hub.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = json.loads(results.read_text("utf-8")) if results.is_file() else {"trials": []}
    done = {(r["scenario"], r["version"]) for r in data["trials"]}
    for scenario in scenarios:
        for version in (CLEAN, *DEFECTS):
            if (scenario.id, version) in done:
                continue
            print(f"[{scenario.id}/{version}]", flush=True)
            base = companies / scenario.business / "company"
            row = await trial(scenario, version, base, output / scenario.id / version, hub)
            print(
                f"  {row['ended']}: rejected={row['rejected']} caught={row['caught']} false_alarm={row['false_alarm']} "
                f"{row['usage']['output']} output tokens, {row['minutes']} min",
                flush=True,
            )
            data["trials"].append(row)
            data["summary"] = summarize(data["trials"])
            results.parent.mkdir(parents=True, exist_ok=True)
            results.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(data.get("summary"), indent=1), flush=True)


if __name__ == "__main__":
    chosen_set = sys.argv[4] if len(sys.argv) > 4 else "first"
    asyncio.run(main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve(), chosen_set))
