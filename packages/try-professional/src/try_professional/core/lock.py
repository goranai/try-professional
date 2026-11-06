"""One command at a time per company. The operating system releases the lock if a process dies."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def _try_lock(descriptor: int) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(descriptor: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def command_lock(home: Path) -> Iterator[bool]:
    """Yields whether this process holds the company. Never waits."""
    home.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(home / "command.lock", os.O_RDWR | os.O_CREAT)
    held = _try_lock(descriptor)
    try:
        yield held
    finally:
        if held:
            _unlock(descriptor)
        os.close(descriptor)
