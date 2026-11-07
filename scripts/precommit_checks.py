"""What runs before each commit: lint, types, and the fast tests. CI keeps the rest.

A test left out here is one that takes long and rarely breaks on its own. Anything to do with a budget, a prompt, the
pipeline or a playbook stays in, because those are what have gone into a commit broken before.

usage: python scripts/precommit_checks.py
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = "packages/try-professional/tests"
SLOW_TESTS = (
    "test_check_model.py",
    "test_clients.py",
    "test_generator.py",
    "test_generator_quality.py",
    "test_loadouts.py",
    "test_mcp_design.py",
    "test_resources.py",
)
WORKERS = "6"


def checks() -> list[tuple[str, list[str]]]:
    python = sys.executable
    pytest = [python, "-m", "pytest", "-m", "not model", "-q", "--no-header", "--tb=short"]
    pytest += [f"--ignore={TESTS}/{name}" for name in SLOW_TESTS]
    # Twice as fast with it, and a contributor who hasn't installed it still gets the check.
    if importlib.util.find_spec("xdist") is not None:
        pytest += ["-n", WORKERS]
    return [
        ("ruff check", [python, "-m", "ruff", "check", ".", "--quiet"]),
        ("ruff format", [python, "-m", "ruff", "format", "--check", ".", "--quiet"]),
        ("mypy", [python, "-m", "mypy"]),
        ("fast tests", pytest),
    ]


def main() -> int:
    # The tests must see this checkout, whatever the shell has set.
    environment = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    environment["PYTHONUTF8"] = "1"
    for name, command in checks():
        print(f"pre-commit: {name}", flush=True)
        done = subprocess.run(command, cwd=ROOT, env=environment, check=False)
        if done.returncode != 0:
            print(f"pre-commit: {name} failed, so nothing was committed.", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
