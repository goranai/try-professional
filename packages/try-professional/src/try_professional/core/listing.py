"""What's in a run's workspace, said briefly enough to hand to every role.

Every assignment listed every file in the workspace. With a repository in inputs/ and a role's copy of it in work/,
that was hundreds of lines in each brief: a planner's was 50 KB and a reviewer's 120 KB, so Antigravity saved them to a
file and read them back in pieces, a dispatch step up to eleven times. A repository, or any large folder, is now one
line; a role with a computer looks inside with its own tools, and one without asks workspace_list.
"""

from __future__ import annotations

from pathlib import Path

from .scratch import SCRATCH_FOLDER, files_under

# A folder with more files than this is one line in a brief.
MAX_LISTED_IN_A_FOLDER = 40
# Never listed: a repository's history, and what a machine makes for itself.
SKIPPED = frozenset({".trypro-scratch", ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
                     ".mypy_cache", ".ruff_cache", ".tox"})  # fmt: skip
# Folders that only hold others, besides the workspace's own: always opened, so what's in them can be told apart.
_HOLDERS = (("inputs", "earlier"),)


def _files(folder: Path) -> list[Path]:
    return [p for p in files_under(folder) if not SKIPPED.intersection(p.relative_to(folder).parts)]


def all_files(root: Path) -> list[str]:
    """Every file someone could want to read, without a repository's history or caches."""
    root = root.resolve()
    if not root.is_dir() or SCRATCH_FOLDER in root.parts:
        return []
    return sorted(p.relative_to(root).as_posix() for p in _files(root))


def brief_listing(root: Path) -> list[str]:
    """Files one per line, except a git repository or a folder of more than MAX_LISTED_IN_A_FOLDER files, which is
    one line saying how many files it holds."""
    root = root.resolve()
    if not root.is_dir() or SCRATCH_FOLDER in root.parts:
        return []
    lines: list[str] = []

    def walk(folder: Path) -> None:
        for child in sorted(folder.iterdir()):
            if child.name in SKIPPED or child.is_symlink() or SCRATCH_FOLDER in child.resolve().parts:
                continue
            relative = child.relative_to(root)
            if child.is_file():
                lines.append(relative.as_posix())
                continue
            if not child.is_dir():
                continue
            repository = (child / ".git").is_dir()
            # The workspace's own folders (inputs/, work/, deliverables/) and inputs/earlier/ only hold others.
            holder = len(relative.parts) == 1 or relative.parts in _HOLDERS
            count = len(_files(child)) if repository or not holder else 0
            if repository or (not holder and count > MAX_LISTED_IN_A_FOLDER):
                kind = ", a git repository" if repository else ""
                lines.append(f"{relative.as_posix()}/ ({count} files{kind})")
            else:
                walk(child)

    walk(root)
    return lines
