"""Stands in for gh and git inside a Codex step. A command that changes something outside waits for a person.

Codex runs a role's commands with nobody to ask, so `gh pr create` or `git push` would just happen. The runner puts
this in front of gh and git on the step's PATH: reading goes through untouched, and a change outside is held, as
`trypro held` shows, exactly as on the API runner when nobody is at the terminal. Like that check, it guards against
mistakes, and isn't a sandbox.

    python -m try_professional.adapters.codex_gate <company> <run_id> <role> <workspace> <real program> [args...]
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path


def held_command(name: str, args: list[str], cwd: Path, workspace: Path) -> str:
    """The command as a person would run it later, from the run's workspace."""
    command = shlex.join([name, *args])
    try:
        relative = cwd.resolve().relative_to(workspace.resolve())
    except ValueError:
        return command
    return command if str(relative) == "." else f"cd {shlex.quote(relative.as_posix())} && {command}"


def main(argv: list[str]) -> int:
    company, run_id, role, workspace, real, *args = argv
    name = Path(real).stem.lower()
    from .api_runner.tools import needs_a_person

    if not needs_a_person(shlex.join([name, *args])):
        return subprocess.call([real, *args])
    from ..cli import _outside_orchestrator

    command = held_command(name, args, Path.cwd(), Path(workspace))
    orchestrator = _outside_orchestrator(Path(company), with_memory=False)
    try:
        number = orchestrator.hold_command(run_id, role, command)
    finally:
        orchestrator.close()
    print(
        f"Not run: this changes something outside, so it waits for a person as #{number} (trypro held). "
        "Carry on without it, and say in your submission that it's waiting.",
        file=sys.stderr,
    )
    return 3


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main(sys.argv[1:]))
