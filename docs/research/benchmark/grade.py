"""Grade the benchmark's runs blind, as DESIGN.md says. On a Claude plan, through claude -p.

usage: python grade.py <out>

Each run's work is stripped of process lines by one rule for every setup, given a random code, and graded twice by
claude-opus-5-5, with no tools, seeing only the goal, its rubric, and the work. A third grade decides where the two
disagree on a must item, or by 2 or more on the holistic score.
"""

from __future__ import annotations

import json
import random
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "journey"))

from goals import GOALS, HOLISTIC, H  # noqa: E402
from run_claude_code import WAIT_FOR_LIMIT_SECONDS, _claude  # noqa: E402

GRADER = "claude-opus-5-5"
SEED = 7
PARALLEL = 4
_LIMIT = re.compile(r"usage limit|rate.?limit|limit reached|hit your limit|too many requests|overloaded", re.IGNORECASE)
# Lines that say who did what in the process, rather than being the work. One rule, for every setup.
_PROCESS = re.compile(
    r"NOT APPROVED|\b(approved|reviewed|checked|signed off) by\b|\breviewers? said\b|\brun-[0-9a-f]{8,}\b|"
    r"^\s*By [\w-]+/[\w-]+\.|\bStatus: (approved|in_review|running|failed|escalated)\b",
    re.IGNORECASE,
)


def strip(text: str, roles: list[str]) -> tuple[str, int]:
    """Remove process lines, including any line that names one of the company's roles. Returns the text, and the
    number of lines removed."""
    kept, removed = [], 0
    for line in text.splitlines():
        if _PROCESS.search(line) or any(ref in line for ref in roles):
            removed += 1
            continue
        kept.append(line)
    return "\n".join(kept).strip(), removed


def blind_work(run_dir: Path, roles: list[str]) -> tuple[str, str, int]:
    parts, removed = [], 0
    for n, path in enumerate(sorted((run_dir / "work_files").glob("*.txt")), start=1):
        body = path.read_text(encoding="utf-8").split("\n\n", 1)[-1]
        text, cut = strip(body, roles)
        removed += cut
        parts.append(f"--- File {n} ---\n{text}")
    closing, cut = strip((run_dir / "closing.md").read_text(encoding="utf-8"), roles)
    return "\n\n".join(parts) or "(no work)", closing or "(no CLOSING.md)", removed + cut


def rubric_text(goal_id: str) -> str:
    spec = GOALS[goal_id]
    lines = ["Must items:"] + [f"- {k}: {v}" for k, v in spec["must"].items()]  # type: ignore[union-attr]
    lines += ["Should items:"] + [f"- {k}: {v}" for k, v in spec["should"].items()]  # type: ignore[union-attr]
    lines += [f"- {H}", HOLISTIC]
    if spec.get("note"):
        lines.append(f"Note: {spec['note']}")
    return "\n".join(lines)


def prompt_for(goal_id: str, work: str, closing: str) -> str:
    must = list(GOALS[goal_id]["must"])  # type: ignore[arg-type]
    should = [*GOALS[goal_id]["should"], "H"]  # type: ignore[misc]
    schema = {
        "must": {k: {"met": "true or false", "why": "one short line"} for k in must},
        "should": {k: {"met": "true or false", "why": "one short line"} for k in should},
        "holistic": "an integer from 1 to 5",
    }
    return (
        "You grade work a business asked for, against a fixed rubric. Judge only what's written below. Each item is "
        "met or not met: when in doubt, not met. The work's own CLOSING.md is graded only by item H.\n\n"
        f"## The goal, with its facts\n{GOALS[goal_id]['goal']}\n\n## The rubric\n{rubric_text(goal_id)}\n\n"
        f"## The work\n{work}\n\n## CLOSING.md\n{closing}\n\n"
        f"Reply with only a JSON object, shaped like this: {json.dumps(schema)}"
    )


def ask(prompt: str) -> dict[str, Any]:
    folder = tempfile.mkdtemp(prefix="trypro-grade-")
    command = [_claude(), "-p", "--model", GRADER, "--output-format", "json", "--tools", "", "--strict-mcp-config",
               "--no-session-persistence"]  # fmt: skip
    for _ in range(50):
        done = subprocess.run(
            command, input=prompt, capture_output=True, text=True, encoding="utf-8", cwd=folder, timeout=1800
        )
        try:
            out = json.loads(done.stdout)
        except json.JSONDecodeError:
            out = {"is_error": True, "result": done.stderr[-300:]}
        text = str(out.get("result", ""))
        if out.get("is_error") and _LIMIT.search(text):
            time.sleep(WAIT_FOR_LIMIT_SECONDS)
            continue
        found = re.search(r"\{.*\}", text, re.DOTALL)
        if found:
            try:
                grade = json.loads(found.group(0))
                grade["_cost_usd"] = out.get("total_cost_usd")
                return grade
            except json.JSONDecodeError:
                pass
    raise RuntimeError("No readable grade.")


def met(grade: dict[str, Any], part: str, key: str) -> bool:
    item = (grade.get(part) or {}).get(key) or {}
    value = item.get("met") if isinstance(item, dict) else item
    return value is True or str(value).lower() == "true"


def settle(goal_id: str, grades: list[dict[str, Any]]) -> dict[str, Any]:
    must = list(GOALS[goal_id]["must"])  # type: ignore[arg-type]
    should = [*GOALS[goal_id]["should"], "H"]  # type: ignore[misc]
    final = {}
    for part, keys in (("must", must), ("should", should)):
        final[part] = {k: sum(met(g, part, k) for g in grades) * 2 > len(grades) for k in keys}
    scores = sorted(int(g.get("holistic") or 1) for g in grades)
    final["holistic"] = scores[len(scores) // 2] if len(scores) % 2 else sum(scores) / len(scores)
    return final


def grade_one(out: Path, run_dir: Path, code: str) -> None:
    target = out / "grades" / f"{code}.json"
    if target.is_file():
        return
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    roles = json.loads((out / "companies" / f"{result['goal']}.json").read_text(encoding="utf-8"))["roles"]
    work, closing, removed = blind_work(run_dir, roles)
    prompt = prompt_for(result["goal"], work, closing)
    (out / "blind").mkdir(exist_ok=True)
    (out / "blind" / f"{code}.md").write_text(f"{work}\n\n## CLOSING.md\n{closing}\n", encoding="utf-8")
    grades = [ask(prompt), ask(prompt)]
    must = list(GOALS[result["goal"]]["must"])  # type: ignore[arg-type]
    disagree = (
        any(met(grades[0], "must", k) != met(grades[1], "must", k) for k in must)
        or abs(int(grades[0].get("holistic") or 1) - int(grades[1].get("holistic") or 1)) >= 2
    )
    if disagree:
        grades.append(ask(prompt))
    record = {"code": code, "goal": result["goal"], "lines_stripped": removed, "grades": grades,
              "first_two_agreed": not disagree, "final": settle(result["goal"], grades)}  # fmt: skip
    target.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"graded {code}", flush=True)


def main(out: Path) -> None:
    runs = sorted(p.parent for p in (out / "runs").glob("*/result.json") if not p.parent.name.startswith("pilot"))
    codes_path = out / "codes.json"
    codes: dict[str, str] = json.loads(codes_path.read_text(encoding="utf-8")) if codes_path.is_file() else {}
    rng = random.Random(SEED)
    for run in runs:
        if run.name not in codes:
            while (code := f"W{rng.randrange(10000, 99999)}") in codes.values():
                pass
            codes[run.name] = code
    codes_path.write_text(json.dumps(codes, indent=1), encoding="utf-8")
    (out / "grades").mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        list(pool.map(lambda r: grade_one(out, r, codes[r.name]), runs))


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
