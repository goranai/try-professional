"""Copy what a journey produced into the repository, so every number in the README can be checked. Free.

usage: python collect_results.py <journey_dir> <output_dir> [--acceptance <acceptance_dir>]

Each command's usage comes from the driver's own record. A command that was stopped and resumed by an older driver only
kept what it spent after resuming, so the part missing from a business's total is added back to its last command.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

KEYS = ("input", "cache_write", "cache_read", "output", "cost_usd_at_list_price", "processes")
BUSINESSES = ("catering", "shorts", "umkm-snacks")


def _reviews(company: Path, run_id: str) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{company / '.trypro' / 'ledger.sqlite'}?mode=ro", uri=True)
    state = json.loads(connection.execute("SELECT state_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()[0])
    verdicts = [a["payload"] for a in state["artifacts"].values() if a["type"] == "ReviewVerdict"]
    return {
        "size": state["size"],
        "verdicts": {
            decision: sum(1 for verdict in verdicts if verdict["decision"] == decision)
            for decision in ("approve", "changes_requested", "block")
        },
        "gates_by_task": {t["task_id"]: t["passed_gates"] for t in state["tasks"].values()},
    }


def _business(folder: Path, target: Path) -> dict[str, Any]:
    progress = json.loads((folder / "progress.json").read_text(encoding="utf-8"))
    commands = progress["commands"]
    missing = {key: round(progress["usage"][key] - sum(c["usage"][key] for c in commands), 5) for key in KEYS}
    if any(missing.values()):
        for key in KEYS:
            commands[-1]["usage"][key] = round(commands[-1]["usage"][key] + missing[key], 5)
        commands[-1]["usage_note"] = "Includes what was spent before a resume. Its minutes cover only the resumed part."
    rows = []
    for command in commands:
        rows.append(
            {
                key: command.get(key)
                for key in (
                    "label", "goal", "status", "minutes", "usage", "usage_note", "tokens_estimated_by_server",
                    "counters", "company_peak_ram_mb", "lowest_free_ram_mb", "final", "by_purpose",
                )
            }
            | _reviews(folder / "company", command["run_id"])
        )  # fmt: skip
        source = folder / "company" / "deliverables" / command["run_id"]
        if source.is_dir():
            shutil.copytree(source, target / folder.name / command["label"], dirs_exist_ok=True)
    return {
        "template": progress.get("template"),
        "design_usage": progress.get("design_usage"),
        "judge_usage": progress.get("judge_usage"),
        "role_processes_stuck": progress.get("stuck"),
        "commands": rows,
    }


def main() -> None:
    journey, output = Path(sys.argv[1]), Path(sys.argv[2])
    deliverables = output / "deliverables"
    results = {business: _business(journey / business, deliverables) for business in BUSINESSES}
    (output / "results_claude_code.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    if len(sys.argv) > 4:
        acceptance = Path(sys.argv[4])
        shutil.copyfile(acceptance / "progress.json", output / "results_acceptance.json")
        runs = acceptance / "company" / "deliverables"
        if runs.is_dir():
            shutil.copytree(runs, deliverables / "acceptance", dirs_exist_ok=True)


if __name__ == "__main__":
    main()
