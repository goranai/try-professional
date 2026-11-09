"""Averages and ranges over repeated runs of the same jobs, from each run's audit log.

usage: python summarize.py <results.json> [label-prefix]

For every job: how many runs finished, the average and the range of minutes of work, model calls, and tokens over the
runs that finished, and each run that didn't, with why. Waiting for a person isn't counted as work.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean
from typing import Any

from try_professional.core.efficiency import measure


def collect(data: dict[str, Any], prefix: str) -> dict[str, list[dict[str, Any]]]:
    jobs: dict[str, list[dict[str, Any]]] = {}
    for label, block in data.items():
        if not label.startswith(prefix):
            continue
        for job, row in block["runs"].items():
            entry = {"label": label, "status": row["status"], "reason": row.get("reason", "")}
            audit = Path(row.get("folder", "")) / ".trypro" / "runs" / row.get("run_id", "") / "audit.jsonl"
            if row.get("run_id") and audit.is_file():
                e = measure(audit, row["run_id"])
                entry.update(minutes=e.working_seconds / 60, calls=e.calls, tokens=e.tokens, rework=e.rework,
                             refused=e.refused, failed=e.failed)  # fmt: skip
            jobs.setdefault(job, []).append(entry)
    return jobs


def _span(values: list[float], unit: str = "", scale: float = 1) -> str:
    if not values:
        return "-"
    shown = [v / scale for v in values]
    return f"{mean(shown):.1f}{unit} ({min(shown):.1f} to {max(shown):.1f})"


def table(jobs: dict[str, list[dict[str, Any]]]) -> str:
    lines = ["| Job | Finished | Minutes, average (range) | Calls | Tokens, thousands |", "|---|---|---|---|---|"]
    notes = []
    for job, runs in jobs.items():
        done = [r for r in runs if r["status"] == "done" and "minutes" in r]
        lines.append(
            f"| {job} | {len(done)} of {len(runs)} | {_span([r['minutes'] for r in done])} | "
            f"{_span([r['calls'] for r in done])} | {_span([r['tokens'] for r in done], scale=1000)} |"
        )
        for r in runs:
            if r["status"] != "done":
                notes.append(f"- {job}, {r['label']}: {r['status']}. {r['reason'][:200]}")
    return "\n".join(lines + (["", "Runs that didn't finish:", *notes] if notes else []))


if __name__ == "__main__":
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(table(collect(data, sys.argv[2] if len(sys.argv) > 2 else "")))
