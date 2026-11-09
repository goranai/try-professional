"""The same real commands, on any installed version of try-professional, measured the same way afterwards.

usage: python measure.py run <out.json> <label> <model>     # with the version you want to measure
       python measure.py report <out.json>                  # with 0.6.2 or later: reads each run's audit log

`run` gives each goal to a new default company, with the shell and the web on, and nobody to approve anything outside,
so posting is held, never done. It keeps the company's folder. `report` then measures every run from its audit log
alone, so versions that never measured themselves are measured exactly like ones that do.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

GOALS = {
    "reply": "Write a two-sentence reply to a customer asking how long delivery takes. Facts: we deliver Monday to "
    "Friday, within 3 working days, and we don't deliver on weekends.",
    "plan": "Write a one-page plan for a bakery's first week on Instagram: five posts, with a caption for each.",
    "fix": "Fix this sentence for a printed menu, and change only what's wrong: 'Our cofee is roasted fresh evry day "
    "and served untill 5pm.'",
    "github": "Use the shell and gh to find one public GitHub repository with more than 1800 stars about Markdown "
    "editors. Report its name, its star count, and the first heading in its README. Don't open issues or PRs.",
    "indonesian": "Tulis balasan dua kalimat untuk pelanggan yang bertanya apakah kami buka hari Minggu. Fakta: kami "
    "buka Senin sampai Sabtu, pukul 08.00 sampai 17.00.",
    "content": "Make content for our bakery's new sourdough and get it ready to post on Instagram: find out what people "
    "like about sourdough, write one post with a caption and hashtags, and say the best time to post it.",
    "contribution": "Find one real typo or broken link in the README of the GitHub repository charmbracelet/glow (read "
    "it with gh or git), check it's really wrong on the main branch and not already reported in its issues, then "
    "open a GitHub issue about it with gh issue create.",
}


def run(out: Path, label: str, model: str) -> None:
    from try_professional import Company, __version__
    from try_professional.config import ModelConfig, build_client, load_env_file

    load_env_file(Path(__file__).resolve().parents[3])
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    rows = data.setdefault(label, {"version": __version__, "model": model, "runs": {}})["runs"]
    client = build_client(ModelConfig(provider="openai_compatible", tiers={}, api_key_env="TRYPRO_LLM_API_KEY",
                                      base_url=os.environ["TRYPRO_LLM_BASE_URL"]))  # fmt: skip
    try:  # Every version this measures has it.
        from try_professional.resources import COMMAND_PEAK_MB, wait_for_memory
    except ImportError:
        wait_for_memory, COMMAND_PEAK_MB = None, 0
    for name, goal in GOALS.items():
        if name in rows:
            continue
        if wait_for_memory is not None:
            # A shared machine: wait for room instead of measuring a run the memory safeguard stops.
            wait_for_memory(COMMAND_PEAK_MB, say=lambda text: print(text, flush=True))
        folder = Path(tempfile.mkdtemp(prefix=f"trypro-eff-{label}-{name}-"))
        company = Company.create(folder / "co", name="Test Co")
        started = time.time()
        try:
            result = company.run(goal, client=client, model=model)
            row: dict[str, Any] = {"status": result.status, "run_id": result.run_id, "reason": (result.reason or "")[:300]}
        except Exception as error:  # A command that errors out is a result too.
            row = {"status": "error", "run_id": "", "reason": f"{type(error).__name__}: {str(error)[:300]}"}
        row.update(seconds=round(time.time() - started), folder=str(folder / "co"))
        if row["reason"].startswith("LowMemory"):
            print(f"{label} {name:14} stopped by the memory safeguard: not a measurement, run it again", flush=True)
            continue
        rows[name] = row
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{label} {name:14} {row['status']:8} {row['seconds']}s {row['reason'][:80]}", flush=True)


def report(out: Path) -> None:
    from try_professional.core.efficiency import measure

    data = json.loads(out.read_text(encoding="utf-8"))
    for label, block in data.items():
        for name, row in block["runs"].items():
            audit = Path(row["folder"]) / ".trypro" / "runs" / row["run_id"] / "audit.jsonl"
            if row.get("run_id") and audit.is_file():
                row["efficiency"] = measure(audit, row["run_id"]).to_dict()
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    labels = list(data)
    print(f"{'goal':14}" + "".join(f"{label:>34}" for label in labels))
    for name in GOALS:
        cells = []
        for label in labels:
            row = data[label]["runs"].get(name, {})
            e = row.get("efficiency") or {}
            cells.append(f"{row.get('status', '-'):>8} {e.get('working_seconds', 0) / 60:5.1f}m {e.get('calls', 0):3}c "
                         f"{e.get('tokens', 0) / 1000:6.0f}k")  # fmt: skip
        print(f"{name:14}" + "".join(f"{cell:>34}" for cell in cells))


if __name__ == "__main__":
    if sys.argv[1] == "run":
        run(Path(sys.argv[2]).resolve(), sys.argv[3], sys.argv[4])
    else:
        report(Path(sys.argv[2]).resolve())
