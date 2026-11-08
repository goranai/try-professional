"""The three-way benchmark, as DESIGN.md freezes it. Runs on a Claude plan through Claude Code, never the paid API.

usage:
    python run_benchmark.py design <out>            Design and approve one company per business, once.
    python run_benchmark.py pilot <out>             One run of each setup on the pilot business. Not reported.
    python run_benchmark.py run <out>               The 54 runs, two at a time, resuming where it stopped.

Each run gets a new folder and a new copy of its company. Results go to <out>/runs/<run>/result.json, and the
work to <out>/runs/<run>/work/. grade.py grades them blind, and report.py writes the results.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "journey"))
sys.path.insert(0, str(HERE))

from goals import BUDGET_USD, GOALS, PILOT, full_text  # noqa: E402
from run_acceptance import ROLE_PREFIX, session  # noqa: E402
from run_claude_code import WAIT_FOR_LIMIT_SECONDS, _claude  # noqa: E402

from try_professional.adapters.mcp.claude_code import server_config, write_claude_code_files  # noqa: E402
from try_professional.cli import _seed_company_memory, _write_project_files  # noqa: E402
from try_professional.config import load_config  # noqa: E402
from try_professional.core.orchestrator import Orchestrator  # noqa: E402
from try_professional.core.pipeline import HumanDecided  # noqa: E402
from try_professional.generator import activate, load_proposal  # noqa: E402
from try_professional.memory import FastEmbedEmbedder  # noqa: E402
from try_professional.memory.embedding import use_embedding_service  # noqa: E402
from try_professional.org import load_org  # noqa: E402

MODEL = "claude-sonnet-5-5"
ROLE_MODEL = os.environ.get("TRYPRO_BENCH_ROLE_MODEL", "sonnet")
CODE_COMMIT = os.environ.get("TRYPRO_BENCH_COMMIT", "")
SETUPS = ("one-agent", "flat-swarm", "hierarchy")
RUNS_PER_CELL = 3
SEED = 20261108
PARALLEL = 2
SWARM_ROUNDS = 6
MAX_OTHER_ANSWERS = 5
MAX_HIERARCHY_SESSIONS = 12
FILE_TOOLS = "Read,Write,Edit,Glob,Grep,Skill"
NO_LIMIT = 50_000_000
_LIMIT = re.compile(r"usage limit|rate.?limit|limit reached|hit your limit|too many requests|overloaded", re.IGNORECASE)
_BUDGET_WORDS = re.compile(r"budget|token|limit|spend|spent|cost", re.IGNORECASE)
DESIGN_PROMPT = (
    "Use the trypro tools to design a company for this business: {business}. Call org_propose, and do what each "
    "answer asks: submit the design with org_proposal_submit, and then judge each role's skills with "
    "org_skill_judgment_submit. When the status is ready_for_a_person, reply with only the proposal id."
)
CONTINUE_PROMPT = (
    "Use the trypro tools to finish designing proposal {proposal}. Call org_proposal_get with it, and do what each "
    "answer asks, until the status is ready_for_a_person. Then reply with only the proposal id."
)
_lock = threading.Lock()


def github_token() -> None:
    """Skill discovery needs a GitHub token. It's read from the GitHub CLI into this process, and never printed."""
    if not os.environ.get("GITHUB_TOKEN"):
        token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
        if not token:
            raise SystemExit("No GitHub token, so companies would get no skills. Run gh auth login first.")
        os.environ["GITHUB_TOKEN"] = token


def stop_services(folder: Path) -> list[int]:
    """Stop the embedding service a run's company started, by its exact process IDs. Only ones under this run's
    own folder, which only this harness started."""
    marker = str(folder).replace("/", "\\").lower()
    listed = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*embed_service*' } | "
         "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"],
        capture_output=True, text=True,
    ).stdout  # fmt: skip
    stopped = []
    for line in listed.splitlines():
        pid, _, command = line.partition("\t")
        if pid.strip().isdigit() and marker in command.replace("/", "\\").lower():
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"Stop-Process -Id {int(pid)} -Confirm:$false -ErrorAction SilentlyContinue",
                ]
            )
            stopped.append(int(pid))
    return stopped


def design_hash() -> str:
    return hashlib.sha256((HERE / "DESIGN.md").read_bytes()).hexdigest()


FROZEN = "FROZEN_SHA256"


def check_frozen() -> str:
    """The harness refuses to run if the design changed after it was frozen."""
    current = design_hash()
    frozen = (HERE / FROZEN).read_text(encoding="utf-8").split()[0] if (HERE / FROZEN).is_file() else ""
    if frozen != current:
        raise SystemExit(f"DESIGN.md isn't the frozen version: {current} against {frozen or 'none'}.")
    return current


# ---- companies -----------------------------------------------------------------------------------------------


def design(out: Path, key: str, business: str) -> Path:
    """One company per business, designed through Claude Code, and approved by this script without edits."""
    root = out / "companies" / key
    meta_path = root.parent / f"{key}.json"
    if meta_path.is_file():
        return root
    root.mkdir(parents=True, exist_ok=True)
    config = root.parent / f"{key}-operator.json"
    config.write_text(json.dumps({"mcpServers": {"trypro": server_config(root)}}), encoding="utf-8")
    flags = ["--mcp-config", str(config), "--strict-mcp-config", "--tools", ""]
    designed = session(DESIGN_PROMPT.format(business=business), root, flags, "mcp__trypro__*", model=MODEL)
    found = re.search(r"prop-[0-9a-f]+", str(designed.get("result")))
    if found is None:
        raise SystemExit(f"No proposal for {key}: {str(designed)[:400]}")
    # A session can stop before its design is ready. Another one picks it up where it stopped.
    for _ in range(4):
        status = json.loads((root / ".trypro" / "proposals" / found.group() / "meta.json").read_text("utf-8"))["status"]
        if status in ("proposed", "approved"):
            break
        more = session(CONTINUE_PROMPT.format(proposal=found.group()), root, flags, "mcp__trypro__*", model=MODEL)
        print(f"{key}: continued a design that was {status}: {str(more.get('result'))[:80]}", flush=True)
    else:
        raise SystemExit(f"{key}'s design never got ready.")
    bundle = load_proposal(root, found.group())
    by = "the benchmark harness, standing in for a person, without edits"
    use_embedding_service(root / ".trypro")
    _write_project_files(root)
    activate(bundle, root, FastEmbedEmbedder(), by=by)
    _seed_company_memory(bundle, root, by)
    raise_limits(root)
    org = load_org(root / "org")
    write_claude_code_files(root, org)
    meta = {
        "business": business,
        "proposal": found.group(),
        "description": org.business.text,
        "roles": list(org.roles),
        "design_usage": designed.get("usage"),
        "loadouts": loadouts(root),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return root


def raise_limits(root: Path) -> None:
    """The shared budget is the only spending limit, so the hierarchy's own limits go above it."""
    import yaml

    path = root / "org" / "org.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    budget = data.setdefault("pipeline", {}).setdefault("budget", {})
    for name in (
        "max_tokens_per_run",
        "max_tokens_per_small_command",
        "mcp_max_tokens_per_run",
        "mcp_max_tokens_per_small_command",
    ):
        budget[name] = NO_LIMIT
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def loadouts(root: Path) -> dict[str, list[str]]:
    from try_professional.cli import _skills

    config = load_config(root)
    org = load_org(config.org_dir)
    skills = _skills(config, org)
    return {ref: [str(s["skill_id"]).rsplit("/", 1)[-1] for s in skills.loadout(ref)] for ref in org.roles}


def company_meta(out: Path, key: str) -> dict[str, Any]:
    return json.loads((out / "companies" / f"{key}.json").read_text(encoding="utf-8"))


REVIEWER = re.compile(r"review|\bqa\b|quality|compliance|counsel|legal", re.IGNORECASE)
SESSION_MODEL = {"hierarchy": MODEL, "hierarchy-mixed": "claude-haiku-5-5", "hierarchy-haiku": "claude-haiku-5-5"}


def is_reviewer(org: Any, ref: str) -> bool:
    """docs/research/mixed_models/DESIGN.md's rule: a review department, or a reviewer by id or title."""
    department, role_id = ref.split("/", 1)
    return bool(REVIEWER.search(department) or REVIEWER.search(role_id) or REVIEWER.search(org.role(ref).title))


def set_role_models(dest: Path, setup: str) -> dict[str, str]:
    """The models each role's subagent runs on, for the mixed-models study. The benchmark itself is all Sonnet."""
    org = load_org(dest / "org")
    chosen = {}
    for ref in org.roles:
        if setup == "hierarchy-haiku" or (setup == "hierarchy-mixed" and is_reviewer(org, ref)):
            chosen[ref] = "haiku"
        else:
            chosen[ref] = "sonnet"
        agent = dest / ".claude" / "agents" / f"trypro-{ref.replace('/', '-')}.md"
        text = agent.read_text(encoding="utf-8")
        agent.write_text(re.sub(r"(?m)^model: \w+$", f"model: {chosen[ref]}", text, count=1), encoding="utf-8")
    return chosen


def fresh_company(out: Path, key: str, dest: Path) -> Path:
    """A new copy of the approved company, with Claude Code's files pointing at the copy."""
    shutil.copytree(out / "companies" / key, dest, ignore=shutil.ignore_patterns("*.lock"))
    # Every role on Sonnet, as the design says. By default a "fast" role would get Haiku.
    write_claude_code_files(dest, load_org(dest / "org"), model=ROLE_MODEL)
    return dest


def skills_folder(company: Path, work: Path) -> list[str]:
    """Every skill the company installed, where Claude Code finds a project's skills."""
    target = work / ".claude" / "skills"
    target.mkdir(parents=True, exist_ok=True)
    names = []
    for skill in sorted((company / "skills" / "installed").rglob("SKILL.md")):
        name = re.sub(r"[^a-z0-9-]+", "-", skill.parent.name.lower()).strip("-") or "skill"
        while name in names:
            name += "-x"
        shutil.copytree(skill.parent, target / name)
        names.append(name)
    return names


# ---- claude -p, for the one agent and the swarm --------------------------------------------------------------------


def claude(prompt: str, cwd: Path, budget: float, resume: str = "") -> dict[str, Any]:
    """One claude -p call with file tools and skills, no MCP, and a spending cap. It waits out a usage limit."""
    command = [
        *[_claude(), "-p", prompt, "--model", MODEL, "--output-format", "json"],
        *["--tools", FILE_TOOLS, "--allowedTools", FILE_TOOLS, "--permission-mode", "acceptEdits"],
        *["--strict-mcp-config", "--max-budget-usd", f"{max(budget, 0.01):.2f}"],
        *(["--resume", resume] if resume else []),
    ]
    environment = {k: v for k, v in os.environ.items() if not k.startswith("TRYPRO_LLM_")}
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
            timeout=3600,
        )
        try:
            result = json.loads(done.stdout)
        except json.JSONDecodeError:
            result = {"is_error": True, "result": (done.stdout + done.stderr)[-500:]}
        if result.get("is_error") and _LIMIT.search(str(result.get("result", ""))):
            print(f"Usage limit, waiting {WAIT_FOR_LIMIT_SECONDS}s", flush=True)
            time.sleep(WAIT_FOR_LIMIT_SECONDS)
            continue
        return result


def questions_in(text: str) -> list[str]:
    return [line.split(":", 1)[1].strip() for line in str(text).splitlines() if line.strip().startswith("QUESTION:")]


class Person:
    """The scripted person: one rule, the same for every setup."""

    def __init__(self) -> None:
        self.log: list[dict[str, str]] = []
        self.others = 0

    def answer(self, question: str, native_kind: str = "") -> str:
        kind = "budget" if _BUDGET_WORDS.search(question) else "other"
        if native_kind == "stuck":
            reply = "retry"
        elif kind == "budget":
            reply = "Continue."
        else:
            self.others += 1
            reply = (
                "Not available." if self.others <= MAX_OTHER_ANSWERS else "No more answers. Finish with what you have."
            )
        self.log.append({"question": question[:2000], "kind": kind, "native_kind": native_kind, "answer": reply})
        return reply


def spend(result: dict[str, Any]) -> float:
    return float(result.get("total_cost_usd") or 0.0)


def usage_of(result: dict[str, Any]) -> dict[str, int]:
    by_model = result.get("modelUsage") or {}
    keys = {"inputTokens": "input", "cacheCreationInputTokens": "cache_write", "cacheReadInputTokens": "cache_read"}
    totals = {name: sum(int(m.get(k, 0)) for m in by_model.values()) for k, name in keys.items()}
    totals["output"] = sum(int(m.get("outputTokens", 0)) for m in by_model.values())
    return totals


def add_usage(into: dict[str, int], more: dict[str, int]) -> None:
    for key, value in more.items():
        into[key] = into.get(key, 0) + value


def capped(result: dict[str, Any], left: float) -> bool:
    return "budget" in str(result.get("subtype", "")).lower() or left <= 0.01


# ---- the three setups --------------------------------------------------------------------------------------------


def one_agent(goal: str, meta: dict[str, Any], company: Path, work: Path, budget: float) -> dict[str, Any]:
    skills = skills_folder(company, work)
    (work / "deliverables").mkdir(exist_ok=True)
    prompt = (
        f"You work for this business: {meta['description']}\n\n{full_text(goal)}\n\n"
        "Write each piece of finished work as its own file in deliverables/. Skills the business installed are "
        "available to you. If you need a fact only the business owner has, end your reply with a line that starts "
        "QUESTION: and stop; you'll get an answer. Otherwise finish the work, and end your reply when it's done."
    )
    person, usage, cost = Person(), {}, 0.0
    result = claude(prompt, work, budget)
    while True:
        cost += spend(result)
        add_usage(usage, usage_of(result))
        asked = questions_in(result.get("result", ""))
        if not asked or capped(result, budget - cost) or result.get("is_error"):
            break
        reply = "\n".join(f"Answer to '{q[:200]}': {person.answer(q)}" for q in asked)
        result = claude(reply, work, budget - cost, resume=str(result.get("session_id", "")))
    finished = (
        not result.get("is_error") and not capped(result, budget - cost) and not questions_in(result.get("result", ""))
    )
    return {"finished": finished, "cost_usd": round(cost, 4), "usage": usage, "questions": person.log,
            "skills": len(skills), "last": str(result.get("result", ""))[-600:]}  # fmt: skip


def role_brief(company: Path, ref: str, loadout: list[str]) -> str:
    import yaml

    dept, role_id = ref.split("/", 1)
    data = yaml.safe_load((company / "org" / "roles" / dept / f"{role_id}.yaml").read_text(encoding="utf-8"))
    return (
        f"You are {data.get('title')} ({ref}). Mission: {data.get('mission')}\n"
        f"Responsibilities: {'; '.join(data.get('responsibilities') or [])}\n"
        f"Skill needs: {'; '.join(data.get('skill_needs') or [])}\n"
        f"Your own skills: {', '.join(loadout) or 'none'}\n{data.get('system_prompt') or ''}".strip()
    )


def snapshot(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((folder / "deliverables").rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(folder).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def flat_swarm(goal: str, meta: dict[str, Any], company: Path, work: Path, budget: float) -> dict[str, Any]:
    skills_folder(company, work)
    (work / "deliverables").mkdir(exist_ok=True)
    board = work / "board.md"
    board.write_text("# Board\n\n", encoding="utf-8")
    roles: list[str] = meta["roles"]
    person, usage, cost, turns = Person(), {}, 0.0, 0
    finished = False
    for round_number in range(1, SWARM_ROUNDS + 1):  # noqa: B007
        before_round = snapshot(work)
        said_done: set[str] = set()
        queue = list(roles)
        while queue:
            ref = queue.pop(0)
            left = budget - cost
            if left <= 0.01:
                break
            prompt = (
                f"{role_brief(company, ref, meta['loadouts'].get(ref, []))}\n\n"
                f"You're one of these roles in a flat team, with no leader and no required review: "
                f"{', '.join(roles)}. You all share this folder, and the board in board.md.\n\n"
                f"The business: {meta['description']}\n\nThe team's goal:\n{full_text(goal)}\n\n"
                f"The board so far:\n{board.read_text(encoding='utf-8')[-12000:]}\n\n"
                "On your turn, do your part, if there's anything for you: write or edit files in deliverables/, and "
                "read any file. Skills are in .claude/skills. Then reply with a short note for the board, under 120 "
                "words. If you need a fact only the business owner has, add a line that starts QUESTION:. End with "
                "exactly one last line: DONE if the whole goal is finished and you have nothing more to add, or "
                "CONTINUE."
            )
            result = claude(prompt, work, left)
            turns += 1
            cost += spend(result)
            add_usage(usage, usage_of(result))
            text = str(result.get("result", ""))
            last = text.strip().splitlines()[-1].strip().upper() if text.strip() else ""
            with board.open("a", encoding="utf-8") as f:
                f.write(f"## {ref}\n{text.strip()}\n\n")
            asked = questions_in(text)
            if asked:
                with board.open("a", encoding="utf-8") as f:
                    for q in asked:
                        f.write(f"## The business owner, to {ref}\n{q[:300]}: {person.answer(q)}\n\n")
                queue.insert(0, ref)
                continue
            if last.startswith("DONE"):
                said_done.add(ref)
        if budget - cost <= 0.01:
            break
        if said_done >= set(roles) and snapshot(work) == before_round:
            finished = True
            break
    return {"finished": finished, "cost_usd": round(cost, 4), "usage": usage, "questions": person.log,
            "turns": turns, "rounds": round_number}  # fmt: skip


def ledger_runs(company: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{company / '.trypro' / 'ledger.sqlite'}?mode=ro", uri=True)
    try:
        return {run_id: json.loads(raw) for run_id, raw in connection.execute("SELECT run_id, state_json FROM runs")}
    finally:
        connection.close()


def decide(company: Path, run_id: str, person: Person) -> int:
    """The scripted person answers a stopped run's questions through the hierarchy's own way of answering."""
    config = load_config(company)
    orchestrator = Orchestrator(load_org(config.org_dir), config.home)
    answered = 0
    try:
        for request in list(orchestrator.state(run_id).human_requests.values()):
            reply = person.answer(request.question, request.kind)
            options = list(request.options)
            if request.kind == "stuck":
                decision = "retry"
            elif "resume" in options:
                decision = "resume"
            elif "retry" in options:
                decision = "retry"
            else:
                # It never waives a review or approves work. What's left ends the work.
                decision = next((o for o in options if o not in ("waive", "approve")), options[0])
            note = "" if decision == "retry" else reply
            orchestrator.handle(
                run_id,
                HumanDecided(request_id=request.id, decision=decision, note=note, decided_by="benchmark script"),
            )
            answered += 1
    finally:
        orchestrator.close()
    return answered


def hierarchy(
    goal: str, meta: dict[str, Any], company: Path, work: Path, budget: float, session_model: str = MODEL
) -> dict[str, Any]:
    person, usage, cost, sessions = Person(), {}, 0.0, 0
    allowed = f"Agent,mcp__trypro__*,{ROLE_PREFIX}*"
    prompt = f"/company {full_text(goal)}"
    run_id = ""
    finished = False
    while sessions < MAX_HIERARCHY_SESSIONS:
        left = budget - cost
        if left <= 0.01:
            break
        flags = ["--max-budget-usd", f"{left:.2f}"]
        result = session(prompt, company, flags, allowed, model=session_model)
        sessions += 1
        session_cost = float((result.get("usage") or {}).get("cost_usd_at_list_price") or 0.0)
        cost += session_cost
        u = result.get("usage") or {}
        add_usage(usage, {k: int(u.get(k) or 0) for k in ("input", "cache_write", "cache_read", "output")})
        runs = ledger_runs(company)
        if not runs:
            break
        run_id = run_id or max(runs, key=lambda r: runs[r].get("seq", 0))
        state = runs[run_id]
        if state["phase"] == "done":
            finished = True
            break
        if state["phase"] == "failed" or budget - cost <= 0.01:
            break
        if state.get("human_requests"):
            decide(company, run_id, person)
        prompt = f"/company continue {run_id}"
    state = ledger_runs(company).get(run_id, {}) if run_id else {}
    legal = any(a.get("produced_by", "").startswith("legal/") for a in state.get("artifacts", {}).values())
    return {"finished": finished, "cost_usd": round(cost, 4), "usage": usage, "questions": person.log,
            "sessions": sessions, "run_id": run_id, "phase": state.get("phase"), "legal_reviewed": legal,
            "report": str(state.get("final_artifact_id"))}  # fmt: skip


SETUP_FN = {
    "one-agent": one_agent,
    "flat-swarm": flat_swarm,
    "hierarchy": hierarchy,
    "hierarchy-mixed": hierarchy,
    "hierarchy-haiku": hierarchy,
}


def collect_work(setup: str, folder: Path, result: dict[str, Any]) -> tuple[list[tuple[str, str]], str]:
    """The final files a run left, and its CLOSING.md. The hierarchy's own report to a person isn't the work."""
    if setup.startswith("hierarchy"):
        base = folder / "deliverables" / str(result.get("run_id"))
        roots = [base / "draft"] if (base / "draft").is_dir() else [base]
    else:
        roots = [folder / "deliverables"]
    files, closing = [], ""
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in (".md", ".txt", ".html", ".csv"):
                continue
            if path.name == "index.md" and setup == "hierarchy":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if path.name.upper() == "CLOSING.MD":
                closing = text
            else:
                files.append((path.relative_to(root).as_posix(), text))
    return files, closing


def one_run(out: Path, run: dict[str, Any], frozen: str) -> None:
    folder = out / "runs" / run["id"]
    result_path = folder / "result.json"
    if result_path.is_file():
        return
    if folder.exists():
        shutil.rmtree(folder)  # An interrupted run starts again from scratch.
    folder.mkdir(parents=True)
    spec = PILOT if run["goal"] == "pilot" else GOALS[run["goal"]]
    meta = company_meta(out, run["goal"])
    budget = BUDGET_USD[str(spec["size"])]
    role_models: dict[str, str] = {}
    if run["setup"].startswith("hierarchy"):
        company = fresh_company(out, run["goal"], folder / "company")
        role_models = set_role_models(company, run["setup"])
        work = company
    else:
        company = out / "companies" / run["goal"]
        work = folder / "work"
        work.mkdir()
    started = time.time()
    print(f"[{run['id']}] start", flush=True)
    if run["setup"].startswith("hierarchy"):
        outcome = hierarchy(str(spec["goal"]), meta, company, work, budget, SESSION_MODEL[run["setup"]])
        outcome["role_models"] = role_models
    else:
        outcome = SETUP_FN[run["setup"]](str(spec["goal"]), meta, company, work, budget)
    outcome["seconds"] = round(time.time() - started)
    if run["setup"].startswith("hierarchy"):
        stop_services(company)
    files, closing = collect_work(run["setup"], work, outcome)
    target = folder / "work_files"
    target.mkdir()
    for n, (name, text) in enumerate(files, start=1):
        (target / f"{n:02d}.txt").write_text(f"{name}\n\n{text}", encoding="utf-8")
    (folder / "closing.md").write_text(closing, encoding="utf-8")
    record = {**run, "design_sha256": frozen, "code_commit": CODE_COMMIT, "role_model": ROLE_MODEL,
              "budget_usd": budget, **outcome, "files": len(files),
              "has_closing": bool(closing.strip())}  # fmt: skip
    result_path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{run['id']}] {'finished' if record['finished'] else 'unfinished'} ${record['cost_usd']}", flush=True)


def plan() -> list[dict[str, Any]]:
    runs = [
        {"id": f"{g}-{s}-{n}", "goal": g, "setup": s, "n": n}
        for g in GOALS
        for s in SETUPS
        for n in range(1, RUNS_PER_CELL + 1)
    ]
    random.Random(SEED).shuffle(runs)
    return runs


def main() -> None:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        raise SystemExit("An Anthropic key is set, which would bill the API. Unset it, so this uses the Claude plan.")
    command, out = sys.argv[1], Path(sys.argv[2]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    frozen = check_frozen()
    github_token()
    if command == "design":
        for key, spec in [*GOALS.items(), ("pilot", PILOT)]:
            print(f"designing {key}", flush=True)
            design(out, key, str(spec["business"]))
        return
    if command == "pilot":
        for setup in SETUPS:
            one_run(out, {"id": f"pilot-{setup}", "goal": "pilot", "setup": setup, "n": 1}, frozen)
        return
    if command == "mixed":
        # After the benchmark: docs/research/mixed_models/DESIGN.md. One run per goal for each.
        runs = [{"id": f"{g}-{s}-1", "goal": g, "setup": s, "n": 1} for g in GOALS
                for s in ("hierarchy-mixed", "hierarchy-haiku")]  # fmt: skip
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            list(pool.map(lambda r: one_run(out, r, frozen), runs))
        return
    if command == "loadouts":
        for meta_path in [*sorted((out / "companies").glob("G*.json")), out / "companies" / "pilot.json"]:
            if "operator" in meta_path.name or not meta_path.is_file():
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["loadouts"] = loadouts(out / "companies" / meta_path.stem)
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
            print(meta_path.stem, sum(len(v) for v in meta["loadouts"].values()), "skill slots")
        return
    if command == "run":
        runs = plan()
        (out / "order.json").write_text(json.dumps([r["id"] for r in runs], indent=1), encoding="utf-8")
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            list(pool.map(lambda r: one_run(out, r, frozen), runs))
        return
    raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
