"""What a small command costs, measured the way the README's cost table was. Uses your Claude plan, not an API key.

usage: python measure.py <companies_dir> <output_dir> <results.json> <label>

Each command runs on a fresh copy of a company from the journey, so earlier runs here can't change it. Every role is its
own `claude -p` process on Sonnet, with only its own server, through `run_claude_code.run_command`. Run it once on the
code before a change and once after, with different labels, into the same results file.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "journey"))

from mcp import Client, StdioServerParameters  # noqa: E402
from run_claude_code import Operator, Progress, run_command  # noqa: E402

from try_professional.adapters.mcp.claude_code import server_config  # noqa: E402
from try_professional.memory.embed_service import ADDRESS_FILE  # noqa: E402
from try_professional.org import load_org  # noqa: E402

# The journey's three small commands, so the numbers sit beside the README's.
COMMANDS = (
    ("catering", "Write a two-sentence reply to a customer asking whether we cover vegetarian meals."),
    ("shorts", "Write one title for a Short about AI note-taking apps."),
    ("umkm-snacks", "Tulis satu balasan singkat untuk pelanggan yang bertanya ongkos kirim ke Surabaya."),
)
_IGNORE = shutil.ignore_patterns(ADDRESS_FILE, "embed-service.lock", "__pycache__")


async def measure(base: Path, folder: Path, goal: str) -> dict[str, Any]:
    root = folder / "company"
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    shutil.copytree(base, root, ignore=_IGNORE)
    progress = Progress(folder / "progress.json")
    config = server_config(root)
    parameters = StdioServerParameters(command=config["command"], args=config["args"], env=dict(os.environ))
    async with Client(parameters) as client:
        row = await run_command(Operator(client), root, "small", goal, progress, load_org(root / "org"))
    return {
        "status": row["status"],
        "usage": row["usage"],
        "by_purpose": row["by_purpose"],
        "steps": sum(bucket["processes"] for bucket in row["by_purpose"].values()),
        "tasks": row["tasks"],
        "counters": row["counters"],
        "estimated_by_server": row["tokens_estimated_by_server"],
        "minutes": row["minutes"],
        "final": row["final"],
    }


def main(companies: Path, output: Path, results: Path, label: str) -> None:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        raise SystemExit("An Anthropic key is set, which would bill the API. Unset it, so this uses your Claude plan.")
    data: dict[str, Any] = json.loads(results.read_text("utf-8")) if results.is_file() else {}
    rows = data.setdefault(label, {})
    for business, goal in COMMANDS:
        if business in rows:
            continue
        print(f"[{label}] {business}: {goal}", flush=True)
        rows[business] = asyncio.run(measure(companies / business / "company", output / label / business, goal))
        print(f"  {rows[business]['status']}, {rows[business]['steps']} steps, {rows[business]['usage']}", flush=True)
        results.parent.mkdir(parents=True, exist_ok=True)
        results.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve(), sys.argv[4])
