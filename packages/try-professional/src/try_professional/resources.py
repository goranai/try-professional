"""Free memory, and not starting work this machine can't afford. Standard library only.

A company shares its machine with whatever else its owner is doing, so the check is on what's free now, and
not on what the machine has. Set TRYPRO_MEMORY_MARGIN_MB to change the safety margin, or TRYPRO_MEMORY_CHECK=off
to turn the check off.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

# See docs/research/memory/. A first generation measured 713 MB, and this rounds up. The others are estimates until
# the B + C and journey harnesses measure them: a later generation adds to a loaded model, and a command loads one.
GENERATION_PEAK_MB = 800
GENERATION_AFTER_MODEL_LOADED_MB = 250
COMMAND_PEAK_MB = 800
SAFETY_MARGIN_MB = 1024
RECHECK_SECONDS = 180
_BYTES_PER_MB = 1024 * 1024


class LowMemory(RuntimeError):
    """Stopped at a safe point because memory ran low. Everything so far is saved, and can be resumed."""


def parse_meminfo(text: str) -> int:
    """MemAvailable from Linux's /proc/meminfo, in MB."""
    found = re.search(r"^MemAvailable:\s+(\d+) kB", text, re.MULTILINE)
    if found is None:
        raise ValueError("no MemAvailable")
    return int(found.group(1)) // 1024


def parse_vm_stat(text: str) -> int:
    """Pages macOS could hand out now, from vm_stat, in MB."""
    size = re.search(r"page size of (\d+) bytes", text)
    if size is None:
        raise ValueError("no page size")
    pages = 0
    for name in ("free", "inactive", "speculative"):
        found = re.search(rf"^Pages {name}:\s+(\d+)\.", text, re.MULTILINE)
        if found is not None:
            pages += int(found.group(1))
    return pages * int(size.group(1)) // _BYTES_PER_MB


def _free_windows() -> int:
    """The lower of free RAM and free commit charge, because running out of either one fails programs."""
    if sys.platform != "win32":
        raise OSError("not Windows")
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = (
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        )

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("GlobalMemoryStatusEx failed")
    return min(int(status.ullAvailPhys), int(status.ullAvailPageFile)) // _BYTES_PER_MB


def free_memory_mb() -> int | None:
    """What could be used now, in MB. None if this system won't say, and then nothing waits on it."""
    try:
        if sys.platform == "win32":
            return _free_windows()
        if sys.platform == "darwin":
            return parse_vm_stat(subprocess.run(["vm_stat"], capture_output=True, text=True, check=True).stdout)
        return parse_meminfo(Path("/proc/meminfo").read_text(encoding="ascii"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def margin_mb() -> int:
    configured = os.environ.get("TRYPRO_MEMORY_MARGIN_MB", "")
    return int(configured) if configured.isdigit() else SAFETY_MARGIN_MB


def _checking() -> bool:
    return os.environ.get("TRYPRO_MEMORY_CHECK", "on").strip().lower() != "off"


def wait_for_memory(
    needed_mb: int,
    *,
    say: Callable[[str], None] = lambda text: None,
    sleep: Callable[[float], None] = time.sleep,
    free: Callable[[], int | None] = free_memory_mb,
    recheck_seconds: int = RECHECK_SECONDS,
) -> None:
    """Hold off before starting until there's room for this work and the margin. Never gives up on its own."""
    if not _checking():
        return
    required = needed_mb + margin_mb()
    waited = False
    while True:
        available = free()
        if available is None or available >= required:
            if waited:
                say(f"{available} MB of memory is free now, so continuing.")
            return
        say(
            f"Only {available} MB of memory is free, and this needs {required} MB: {needed_mb} MB for the work "
            f"and {margin_mb()} MB as a safety margin. Nothing has started. Checking again in "
            f"{recheck_seconds // 60} minutes. Press Ctrl+C to stop."
        )
        waited = True
        sleep(recheck_seconds)


def stop_if_memory_low(free: Callable[[], int | None] = free_memory_mb) -> None:
    """For a safe point in work already under way. Raises LowMemory when free memory is under the margin."""
    if not _checking():
        return
    available = free()
    if available is not None and available < margin_mb():
        raise LowMemory(f"Only {available} MB of memory is free, under the {margin_mb()} MB safety margin.")
