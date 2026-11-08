"""A tool your agent calls to hand a goal to the company, and get back what it made.

usage: python delegate.py <company folder> "<goal>"

It runs /company in Claude Code, in the company's folder, on your Claude plan: each role is its own subagent, with
only its own role's server. Then it reads the result through the stable API. If the company stops for a person, it
says so, and a person answers with `trypro decide` in that folder.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from try_professional import Company


def delegate(folder: Path, goal: str, *, budget_usd: float = 2.0) -> dict[str, Any]:
    company = Company.open(folder)
    claude = shutil.which("claude") or "claude"
    allowed = "Agent,mcp__trypro__*,mcp__trypro_role__*"
    subprocess.run(
        [claude, "-p", f"/company {goal}", "--allowedTools", allowed, "--max-budget-usd", str(budget_usd)],
        cwd=company.root,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    runs = company.runs()
    if not runs:
        return {"status": "nothing started"}
    run_id = runs[0]
    status = company.status(run_id)
    return {
        "run_id": run_id,
        "status": status.phase,
        "questions_for_a_person": status.questions,
        "deliverables": str(company.deliverables(run_id)),
        "report": company.final_report(run_id),
    }


if __name__ == "__main__":
    print(json.dumps(delegate(Path(sys.argv[1]), sys.argv[2]), ensure_ascii=False, indent=1, default=str))
