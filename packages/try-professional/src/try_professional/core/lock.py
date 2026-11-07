"""One command at a time per company. The operating system releases the lock if a process dies."""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

STATE_LOCK_SECONDS = 60


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


class StateLock:
    """Only one process, and one thread, changes a company's state at a time. Reentrant.

    Reading a run, changing it, and saving it must happen as one step, or two servers overwrite each other.
    """

    def __init__(self, home: Path) -> None:
        self._path = home / "state.lock"
        self._thread = threading.RLock()
        self._depth = 0
        self._descriptor = -1

    def __enter__(self) -> StateLock:
        self._thread.acquire()
        if self._depth == 0:
            try:
                self._acquire()
            except BaseException:
                self._thread.release()
                raise
        self._depth += 1
        return self

    def _acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self._path, os.O_RDWR | os.O_CREAT)
        deadline = time.monotonic() + STATE_LOCK_SECONDS
        while not _try_lock(descriptor):
            if time.monotonic() > deadline:
                os.close(descriptor)
                raise TimeoutError(f"Another process has held {self._path} for {STATE_LOCK_SECONDS}s.")
            time.sleep(0.05)
        self._descriptor = descriptor

    def __exit__(self, *_: object) -> None:
        self._depth -= 1
        if self._depth == 0:
            _unlock(self._descriptor)
            os.close(self._descriptor)
            self._descriptor = -1
        self._thread.release()


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
