"""Run-owned temporary storage, kept apart from files handed to a person.

Clones accumulated when cleanup was assigned to roles, and environments in workspaces broke later runs on Windows.
The engine owns a short, stable path instead, shared by the processes serving the run.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

SCRATCH_FOLDER = ".trypro-scratch"
SCRATCH_ENV = "TRYPRO_RUN_SCRATCH"
_SYSTEM_SCRATCH_ROOT = Path(tempfile.gettempdir()).resolve() / SCRATCH_FOLDER


def scratch_root() -> Path:
    # Resolve the system temp location once: legitimate aliases are common, and steps can change temp settings.
    return _SYSTEM_SCRATCH_ROOT


def _owner(home: Path, run_id: str) -> str:
    owner = f"{home.resolve()}\n{run_id}".encode()
    return hashlib.sha256(owner).hexdigest()[:24]


def record_path(home: Path, run_id: str) -> Path:
    return home / "runs" / run_id / "scratch.json"


def scratch_path(home: Path, run_id: str, allocated: Path | None) -> Path:
    record = record_path(home, run_id)
    if record.exists():
        try:
            value = json.loads(record.read_text(encoding="utf-8"))["path"]
            if not isinstance(value, str):
                raise ValueError("path must be a string")
            path = Path(value)
        except (ValueError, TypeError, KeyError) as error:
            raise OSError("scratch allocation record is invalid; cleanup refused") from error
        if allocated is None or path != allocated:
            raise OSError("scratch allocation record does not belong to this run; cleanup refused")
    path = allocated if allocated is not None else scratch_root() / _owner(home, run_id)
    if not path.is_absolute() or path.name != _owner(home, run_id) or SCRATCH_FOLDER not in path.parts:
        raise OSError("scratch allocation does not belong to this run; cleanup refused")
    return path


def _check_path(path: Path) -> None:
    # A role can replace a folder with a link or junction. Never follow that replacement during deletion.
    if path.resolve() != path.absolute():
        raise OSError("scratch path was redirected; cleanup refused")


def allocate(home: Path, run_id: str, allocated: Path | None) -> Path:
    path = scratch_path(home, run_id, allocated)
    if path.is_relative_to(home.resolve()) or home.resolve().is_relative_to(path):
        raise OSError("scratch must be outside the company state")
    _check_path(path)
    path.mkdir(parents=True, exist_ok=True)
    # A different role server can have a different temp environment. The original engine allocation stays authoritative.
    record = record_path(home, run_id)
    record.parent.mkdir(parents=True, exist_ok=True)
    temporary = record.with_suffix(".tmp")
    temporary.write_text(json.dumps({"path": str(path)}) + "\n", encoding="utf-8")
    temporary.replace(record)
    return path


def cleanup(home: Path, run_id: str, allocated: Path | None) -> bool:
    if allocated is None:
        raise OSError("scratch has no engine-owned allocation; cleanup refused")
    path = scratch_path(home, run_id, allocated)
    if path.is_relative_to(home.resolve()) or home.resolve().is_relative_to(path):
        raise OSError("scratch must be outside the company state; cleanup refused")
    _check_path(path)
    if not path.exists():
        return False

    def retry_readonly(function: Callable[..., Any], name: str, error: tuple[Any, Any, Any]) -> None:
        # Git marks object files read-only on Windows. Retry only within this run, never on a redirected path.
        target = Path(name)
        if not isinstance(error[1], PermissionError) or not target.resolve().is_relative_to(path):
            raise error[1]
        _check_path(path)
        target.chmod(stat.S_IREAD | stat.S_IWRITE)
        function(name)

    shutil.rmtree(path, onerror=retry_readonly)
    return True


def scratch_instructions(path: Path) -> str:
    return (
        f"This run's scratch folder: {path}\n"
        f"Every step receives it as {SCRATCH_ENV}. Put clones, virtualenvs and temporary files there. "
        "Hand in deliverables through workspace_write. Scratch is never exported, listed or copied into later runs. "
        "The engine cleans it when the run ends; never assign cleanup to a role."
    )


def paths_under(root: Path) -> Iterator[Path]:
    """Prune scratch before traversing: even inspecting a virtualenv's long paths can fail on Windows."""
    resolved_root = root.resolve()
    if SCRATCH_FOLDER in resolved_root.parts:
        return
    for folder, directories, files in os.walk(root, followlinks=False):
        parent = Path(folder)
        directories[:] = [
            name
            for name in directories
            if name != SCRATCH_FOLDER
            and SCRATCH_FOLDER not in (parent / name).resolve().parts
            and (parent / name).resolve().is_relative_to(resolved_root)
        ]
        for name in directories:
            if not (parent / name).is_symlink():
                yield parent / name
        for name in files:
            path = parent / name
            if not path.is_symlink() and path.resolve().is_relative_to(resolved_root):
                yield path


def files_under(root: Path) -> Iterator[Path]:
    return (path for path in paths_under(root) if path.is_file())
