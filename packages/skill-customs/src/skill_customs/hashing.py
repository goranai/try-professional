"""A hash of a whole skill folder. Any added, removed, renamed, or edited file changes it."""

from __future__ import annotations

import hashlib
from pathlib import Path


def content_hash(root: Path) -> str:
    """Sorted relative paths and file hashes, hashed together. Symlinks are hashed by their target text."""
    digest = hashlib.sha256()
    files = sorted(p for p in root.rglob("*") if p.is_file() or p.is_symlink())
    for path in files:
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            content = hashlib.sha256(str(path.readlink()).encode("utf-8")).hexdigest()
        else:
            content = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(f"{relative}\0{content}\n".encode())
    return digest.hexdigest()
