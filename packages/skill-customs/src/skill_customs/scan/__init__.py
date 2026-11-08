"""Static scanning, and the plugin point for other scanners. Nothing in a skill is ever executed."""

from __future__ import annotations

import base64
import binascii
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

from ..policy import Policy
from .rules import (
    _BASE64_RUN,
    _BLOB,
    _DATA_URI,
    _HEX_RUN,
    _INVISIBLE,
    _LATIN,
    _OTHER_SCRIPT,
    _STRIP,
    _ZERO_WIDTH,
    BLOCK,
    CONFUSABLES,
    EXECUTABLE_DIRS,
    EXECUTABLE_SUFFIXES,
    NOTE,
    REVIEW,
    RULES,
    WINDOW_LINES,
    WINDOW_RULES,
    Rule,
)

SCANNER_VERSION = "0.1.1"
RULESET = "2026.11.2"
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
        """block, review, or pass. pass means no known signal, and doesn't mean safe. A note never changes it."""
        if any(f.severity == BLOCK for f in self.findings):
            return BLOCK
        if any(f.severity == REVIEW for f in self.findings) or self.executable_files:
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


def _mixed_words(text: str) -> list[str]:
    """Words that mix Latin letters with Cyrillic or Greek ones: how a look-alike letter hides a word from a pattern."""
    return [w for w in re.findall(r"\w+", text) if _LATIN.search(w) and _OTHER_SCRIPT.search(w)]


def _normalize(line: str) -> str:
    """Undo tricks that keep text readable to a model but hide it from a pattern."""
    without_escapes = _ANSI.sub(" ", line.translate(_STRIP))
    without_controls = "".join(" " if unicodedata.category(c) == "Cc" else c for c in without_escapes)
    text = unicodedata.normalize("NFKC", without_controls)
    for word in set(_mixed_words(text)):
        text = text.replace(word, word.translate(CONFUSABLES))
    return text.casefold()


_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_QUOTE = re.compile(r"^\s{0,3}>")
_REFERENCE = re.compile(r"^\s*([-*+]|\d+[.)])\s+.*\]\(https?://")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_QUOTED = re.compile(r'"[^"\n]{3,}"|\u201c[^\u201d\n]{3,}\u201d')
_FRONTMATTER_KEY = re.compile(r"^([A-Za-z_][\w-]*)\s*:")


@dataclass(frozen=True)
class _Line:
    number: int
    raw: str
    text: str  # normalized
    context: str  # prose, code, doc, name, description, frontmatter


def _lines(text: str) -> list[_Line]:
    """Each line, with where it sits. Code is where an instruction to run something usually is, so it stays prose."""
    out: list[_Line] = []
    raw_lines = text.splitlines()
    in_front = bool(raw_lines) and raw_lines[0].strip() == "---"
    in_fence = False
    for number, raw in enumerate(raw_lines, start=1):
        if in_front:
            if number > 1 and raw.strip() in ("---", "..."):
                in_front = False
                out.append(_Line(number, raw, _normalize(raw), "frontmatter"))
                continue
            key = _FRONTMATTER_KEY.match(raw)
            field = key.group(1).lower() if key else ""
            context = field if field in ("name", "description") else "frontmatter"
            out.append(_Line(number, raw, _normalize(raw), context))
            continue
        if _FENCE.match(raw):
            in_fence = not in_fence
            out.append(_Line(number, raw, _normalize(raw), "code"))
            continue
        if in_fence:
            context = "code"
        elif _HEADING.match(raw) or _TABLE_ROW.match(raw) or _QUOTE.match(raw) or _REFERENCE.match(raw):
            context = "doc"
        else:
            context = "prose"
        out.append(_Line(number, raw, _normalize(raw), context))
    return out


def _severity(rule: Rule, line: _Line, start: int, end: int) -> str:
    """Where a match sits changes how sure it is. A person still reads every finding."""
    if line.context == "name":
        # A skill's name is a label. It can name a topic, like jailbreaks, without asking for anything.
        return NOTE
    if rule.severity != BLOCK:
        return rule.severity
    if line.context in ("description", "doc"):
        # A description reaches the model, and so do headings and tables, so these stay findings, for a person.
        return REVIEW
    if any(q.start() <= start and end <= q.end() for q in _QUOTED.finditer(line.text)):
        return REVIEW
    return BLOCK


def _decoded(text: str) -> list[str]:
    """Readable text hidden in base64 or hex. Binary, like a hash or an image, isn't text, and is skipped."""
    found = []
    for run in (*_BASE64_RUN.findall(text), *_HEX_RUN.findall(text)):
        for decode in (_from_hex, _from_base64):
            plain = decode(run)
            if plain and sum(c.isprintable() or c.isspace() for c in plain) >= 0.9 * len(plain) and " " in plain:
                found.append(plain)
                break
    return found


def _from_base64(run: str) -> str | None:
    try:
        padded = run + "=" * (-len(run) % 4)
        raw = base64.b64decode(padded.replace("-", "+").replace("_", "/"), validate=True)
        return raw.decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None


def _from_hex(run: str) -> str | None:
    if len(run) % 2 or not re.fullmatch(r"[0-9a-fA-F]+", run):
        return None
    try:
        return bytes.fromhex(run).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


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
        lines = _lines(text)
        matched: set[tuple[str, int]] = set()
        for line in lines:
            findings += self._hidden_characters(relative, line.number, line.raw)
            if _mixed_words(unicodedata.normalize("NFKC", line.raw)):
                message = "Mixes look-alike letters from different alphabets, which can hide a word"
                findings.append(
                    Finding("mixed-script", REVIEW, relative, line.number, message, excerpt=_safe_excerpt(line.raw))
                )
            for rule in RULES:
                hit = rule.pattern.search(line.text)
                if hit:
                    matched.add((rule.id, line.number))
                    severity = _severity(rule, line, hit.start(), hit.end())
                    findings.append(
                        Finding(rule.id, severity, relative, line.number, rule.message, excerpt=_safe_excerpt(line.raw))
                    )
            for plain in _decoded(line.raw):
                normalized = _normalize(" ".join(plain.split()))
                for rule in RULES:
                    if rule.pattern.search(normalized):
                        message = f"{rule.message}, hidden in encoded text"
                        findings.append(Finding(rule.id, rule.severity, relative, line.number, message))
            if _BLOB.search(line.raw) or _DATA_URI.search(line.raw):
                message = "Long encoded data can hide content"
                findings.append(Finding("encoded-blob", REVIEW, relative, line.number, message))
        return findings + self._across_lines(relative, lines, matched)

    def _across_lines(self, relative: str, lines: list[_Line], matched: set[tuple[str, int]]) -> list[Finding]:
        """An instruction split over a few lines of one paragraph. Reported once, at its first line."""
        findings: list[Finding] = []
        reported: set[tuple[str, int]] = set()
        prose = [line for line in lines if line.context in ("prose", "doc", "code")]
        for index, first in enumerate(prose):
            window = [first]
            for following in prose[index + 1 : index + WINDOW_LINES]:
                if following.number != window[-1].number + 1 or not following.raw.strip():
                    break
                window.append(following)
            if len(window) < 2 or not first.raw.strip():
                continue
            joined = _Line(
                first.number, " ".join(w.raw for w in window), " ".join(w.text for w in window), first.context
            )
            if all(w.context == "doc" for w in window):
                joined = _Line(joined.number, joined.raw, joined.text, "doc")
            span = range(first.number, window[-1].number + 1)
            for rule in RULES:
                if not rule.id.startswith(WINDOW_RULES) or any((rule.id, n) in matched for n in span):
                    continue
                if any((rule.id, n) in reported for n in span):
                    continue
                hit = rule.pattern.search(joined.text)
                if hit:
                    reported.update((rule.id, n) for n in span)
                    findings.append(
                        Finding(
                            rule.id,
                            _severity(rule, joined, hit.start(), hit.end()),
                            relative,
                            first.number,
                            f"{rule.message}, across {len(window)} lines",
                            excerpt=_safe_excerpt(joined.raw),
                        )
                    )
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
    "NOTE",
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
