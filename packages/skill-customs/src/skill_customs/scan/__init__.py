"""Static scanning, and the plugin point for other scanners. Nothing in a skill is ever executed."""

from __future__ import annotations

import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

from ..policy import Policy
from .rules import (
    _BLOB,
    _DATA_URI,
    _INVISIBLE,
    _STRIP,
    _ZERO_WIDTH,
    BLOCK,
    EXECUTABLE_DIRS,
    EXECUTABLE_SUFFIXES,
    REVIEW,
    RULES,
)

SCANNER_VERSION = "0.1.0"
RULESET = "2026.11"
ENTRY_POINT_GROUP = "skill_customs.scanners"
_EXCERPT = 80
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\\\)?")


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str  # block | review
    file: str
    line: int
    message: str
    scanner: str = "builtin"
    excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        """For the lockfile. Excerpts stay out, because they are untrusted text."""
        return {
            "rule": self.rule,
            "severity": self.severity,
            "file": self.file,
            "line": self.line,
            "scanner": self.scanner,
        }


@dataclass
class ScanReport:
    findings: list[Finding] = field(default_factory=list)
    executable_files: list[str] = field(default_factory=list)
    files_scanned: int = 0
    scanners: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """block, review, or pass. pass means no known signal, and doesn't mean safe."""
        if any(f.severity == BLOCK for f in self.findings):
            return BLOCK
        if self.findings or self.executable_files:
            return REVIEW
        return "pass"

    @property
    def exit_code(self) -> int:
        return {"pass": 0, REVIEW: 1, BLOCK: 2}[self.verdict]


def decide_trust(report: ScanReport) -> str:
    """indexed only if the skill is plain instructions with no findings. Everything else waits for a human."""
    return "indexed" if report.verdict == "pass" else "quarantined"


class Scanner(Protocol):
    """Implement this to add a scanner. A scanner must never execute anything it finds in the folder."""

    name: str

    def scan(self, root: Path) -> list[Finding]: ...


def _normalize(line: str) -> str:
    """Undo tricks that keep text readable to a model but hide it from a pattern."""
    without_escapes = _ANSI.sub(" ", line.translate(_STRIP))
    without_controls = "".join(" " if unicodedata.category(c) == "Cc" else c for c in without_escapes)
    return unicodedata.normalize("NFKC", without_controls).casefold()


def _safe_excerpt(text: str) -> str:
    """Skill text is untrusted, and this reaches a terminal: no control or invisible characters."""
    cleaned = "".join(c if c.isprintable() and ord(c) not in _STRIP else " " for c in _ANSI.sub(" ", text))
    return cleaned.strip()[:_EXCERPT]


class BuiltinScanner:
    name = "builtin"

    def __init__(self, policy: Policy | None = None) -> None:
        self.policy = policy or Policy.default()

    def scan(self, root: Path) -> list[Finding]:
        findings: list[Finding] = []
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                findings.append(Finding("symlink", BLOCK, relative, 0, "A link could point outside the skill"))
                continue
            if not path.is_file():
                continue
            if path.stat().st_size > self.policy.max_file_bytes:
                findings.append(Finding("oversized-file", REVIEW, relative, 0, "Too large to scan"))
                continue
            data = path.read_bytes()
            if b"\0" in data[:8192]:
                findings.append(Finding("binary-file", REVIEW, relative, 0, "Binary content can't be scanned"))
                continue
            findings += self._scan_text(relative, data.decode("utf-8", errors="replace"))
        return findings

    def _scan_text(self, relative: str, text: str) -> list[Finding]:
        findings: list[Finding] = []
        for number, line in enumerate(text.splitlines(), start=1):
            hidden = self._hidden_characters(relative, number, line)
            findings += hidden
            normalized = _normalize(line)
            for rule in RULES:
                if rule.pattern.search(normalized):
                    findings.append(
                        Finding(rule.id, rule.severity, relative, number, rule.message, excerpt=_safe_excerpt(line))
                    )
            if _BLOB.search(line) or _DATA_URI.search(line):
                findings.append(Finding("encoded-blob", REVIEW, relative, number, "Long encoded data can hide content"))
        return findings

    def _hidden_characters(self, relative: str, number: int, line: str) -> list[Finding]:
        found = []
        kinds = {_INVISIBLE[ord(c)] for c in line if ord(c) in _INVISIBLE}
        for kind in sorted(kinds):
            found.append(Finding(kind, BLOCK, relative, number, "Hidden characters can conceal instructions"))
        body = line[1:] if number == 1 and line.startswith("﻿") else line
        if any(ord(c) in _ZERO_WIDTH for c in body):
            found.append(Finding("zero-width", REVIEW, relative, number, "Invisible characters in the text"))
        return found


def find_executable_files(root: Path) -> list[str]:
    """Anything that looks runnable: by name, location, or a shebang line. Nothing is run to check."""
    found = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        parts = {part.lower() for part in relative.parts[:-1]}
        shebang = False
        try:
            with path.open("rb") as handle:
                shebang = handle.read(2) == b"#!"
        except OSError:
            pass
        if path.suffix.lower() in EXECUTABLE_SUFFIXES or parts & EXECUTABLE_DIRS or shebang:
            found.append(relative.as_posix())
    return found


class CommandScanner:
    """Run an external scanner on the folder. Exit 0 means clean, and anything else means a human looks.

    It runs the scanner, never the skill. Its output isn't interpreted, because it may quote skill text.
    """

    def __init__(self, name: str, argv: tuple[str, ...], timeout: float = 300.0) -> None:
        self.name = name
        self.argv = argv
        self.timeout = timeout

    def scan(self, root: Path) -> list[Finding]:
        result = subprocess.run([*self.argv, str(root)], capture_output=True, timeout=self.timeout, check=False)
        if result.returncode == 0:
            return []
        message = f"External scanner exited with {result.returncode}"
        return [Finding(f"{self.name}-flagged", REVIEW, ".", 0, message, scanner=self.name)]


def _configured_scanners(policy: Policy) -> list[Scanner]:
    """Plugins run only if the policy names them. Installing a package doesn't turn one on."""
    scanners: list[Scanner] = []
    if policy.scanners:
        available = {entry.name: entry for entry in metadata.entry_points(group=ENTRY_POINT_GROUP)}
        for name in policy.scanners:
            if name not in available:
                raise ValueError(f"scanner '{name}' isn't installed")
            scanners.append(available[name].load()())
    for name, argv in policy.command_scanners.items():
        scanners.append(CommandScanner(name, argv))
    return scanners


def scan(root: Path, policy: Policy | None = None, *, scanners: list[Scanner] | None = None) -> ScanReport:
    """Run the built-in scanner plus any others. A scanner that crashes counts as a blocking finding."""
    active = policy or Policy.default()
    report = ScanReport(executable_files=find_executable_files(root))
    report.files_scanned = sum(1 for p in root.rglob("*") if p.is_file())
    for scanner in [BuiltinScanner(active), *_configured_scanners(active), *(scanners or [])]:
        report.scanners.append(scanner.name)
        try:
            report.findings += scanner.scan(root)
        except Exception as error:
            message = f"Scanner failed: {type(error).__name__}"
            report.findings.append(Finding("scanner-error", BLOCK, ".", 0, message, scanner=scanner.name))
    for relative in report.executable_files:
        report.findings.append(Finding("executable-content", REVIEW, relative, 0, "Could run code if something ran it"))
    return report


__all__ = [
    "RULESET",
    "SCANNER_VERSION",
    "BuiltinScanner",
    "CommandScanner",
    "Finding",
    "ScanReport",
    "Scanner",
    "decide_trust",
    "find_executable_files",
    "scan",
]
