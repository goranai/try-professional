"""Peak RAM of the steps that load the embedding model, measured from outside each process. Free: no paid API.

usage:
    python measure_peak_ram.py <work_dir> [--discovery]

Each step runs in a fresh process, and this script reads that process's peak from the operating system,
including any children. --discovery also lets `trypro init` search GitHub and unpack real skill repositories,
which needs GITHUB_TOKEN.

Needs psutil, which is a measuring tool and not a dependency of Try Professional.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil

from try_professional.resources import GENERATION_PEAK_MB, free_memory_mb, wait_for_memory

TIME_LIMIT_SECONDS = 900
_LOAD = """
from try_professional.memory import FastEmbedEmbedder
FastEmbedEmbedder().embed_query("ready")
"""
_SKILLS = """
from try_professional.memory import FastEmbedEmbedder
text = "brief a video thumbnail, write its copy, and check the result against the brand guide. " * 12
FastEmbedEmbedder().embed_documents([text] * 60)
"""
_MEMORY = """
import sys, tempfile
from pathlib import Path
from try_professional.memory import FastEmbedEmbedder, MemorySettings, MnemosyneBackend
embedder = FastEmbedEmbedder()
embedder.embed_query("ready")
backend = MnemosyneBackend(MemorySettings(data_dir=Path(sys.argv[1])))
backend.remember("company", "We ship on Mondays.", source="test")
backend.recall("company", "When do we ship?", 3)
embedder.embed_query("still here")
"""
_INIT_THEN_RUN = """
import os, sys
from try_professional.cli import main
os.chdir(sys.argv[1])
assert main(["init", "--skip-model"]) == 0
sys.exit(main(["run", "--dry-run", "Build a health check endpoint"]))
"""
_CLI = "import sys; from try_professional.cli import main; sys.exit(main(sys.argv[1:]))"


def _peak_of(command: list[str], *, cwd: Path, env: dict[str, str]) -> dict[str, object]:
    log = tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace")
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT
    )
    watched = psutil.Process(process.pid)
    peak = 0
    started = time.monotonic()
    while process.poll() is None:
        if time.monotonic() - started > TIME_LIMIT_SECONDS:
            for item in [*watched.children(recursive=True), watched]:
                item.kill()
            break
        try:
            total = 0
            for item in [watched, *watched.children(recursive=True)]:
                info = item.memory_info()
                total += getattr(info, "peak_wset", info.rss)
            peak = max(peak, total)
        except psutil.Error:
            pass
        time.sleep(0.1)
    process.wait()
    log.seek(0)
    output = log.read()
    log.close()
    return {
        "peak_mb": round(peak / 1024 / 1024),
        "seconds": round(time.monotonic() - started),
        "exit_code": process.returncode,
        "tail": output[-300:],
    }


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--discovery"]
    discovery = "--discovery" in sys.argv
    work = Path(args[0])
    work.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    python = sys.executable
    steps: dict[str, list[str]] = {
        "import the CLI, with no model": [python, "-c", "import try_professional.cli"],
        "load the model, one query": [python, "-c", _LOAD],
        "embed 60 long skill descriptions": [python, "-c", _SKILLS],
        "our embedder plus company memory": [python, "-c", _MEMORY, str(work / "memory")],
    }
    template = work / "template"
    template.mkdir(exist_ok=True)
    steps["trypro init --template, no discovery"] = [
        python, "-c", _CLI, "init", "--template", "marketing-agency", "--no-discover", "--dir", str(template),
    ]  # fmt: skip
    if discovery:
        real = work / "discovery"
        real.mkdir(exist_ok=True)
        steps["trypro init --dry-run, real GitHub discovery"] = [
            python, "-c", _CLI, "init", "a beekeeping supply shop", "--dry-run", "--dir", str(real),
        ]  # fmt: skip
    company = work / "command"
    company.mkdir(exist_ok=True)
    steps["trypro init, then trypro run --dry-run"] = [
        python, "-c", _INIT_THEN_RUN, str(company),
    ]  # fmt: skip
    results = {}
    env["PYTHONUNBUFFERED"] = "1"
    for name, command in steps.items():
        # Measuring must not be what pushes a busy machine over.
        wait_for_memory(GENERATION_PEAK_MB, say=lambda text: print(text, flush=True))
        free_before = free_memory_mb()
        results[name] = _peak_of(command, cwd=work, env=env)
        results[name]["free_mb_before"] = free_before
        print(f"{name}: {results[name]}", flush=True)
    left = [p.name for p in Path(tempfile.gettempdir()).glob("trypro-skills-*")]
    left += [p.name for p in Path(tempfile.gettempdir()).glob("skill-customs-*")]
    summary = {"results": results, "temp_folders_left": left}
    (work / "peak_ram.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    shutil.rmtree(work / "memory", ignore_errors=True)


if __name__ == "__main__":
    main()
