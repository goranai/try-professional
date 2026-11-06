"""Scan the working tree and full git history for secrets. Runs before every push.

Checks two things: common key patterns, and the exact values of secret-looking
variables in the local .env. It reports file and rule only, and never prints a match.

Exit codes: 0 clean, 1 found something, 2 could not scan.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

PATTERNS = {
    "generic-sk-key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "github-token": re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "slack-token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "bearer-token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{24,}"),
    "assigned-api-key": re.compile(
        r"""(?i)\b(api[_-]?key|secret|access[_-]?token)\b["']?\s*[:=]\s*["'][A-Za-z0-9._~+/-]{24,}["']"""
    ),
}
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)
MIN_SECRET_LENGTH = 8


@dataclass(frozen=True)
class Finding:
    where: str
    location: str
    rule: str

    def __str__(self) -> str:
        return f"{self.where}: {self.location} [{self.rule}]"


def env_secrets(path: Path) -> dict[str, str]:
    """Values of secret-looking variables in .env, so the real key is searched for exactly."""
    secrets: dict[str, str] = {}
    if not path.is_file():
        return secrets
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.removeprefix("export ").partition("=")
        value = value.strip().strip("'\"")
        if SECRET_NAME.search(name) and len(value) >= MIN_SECRET_LENGTH:
            secrets[name.strip()] = value
    return secrets


def scan_text(text: str, secrets: dict[str, str]) -> set[str]:
    rules = {name for name, pattern in PATTERNS.items() if pattern.search(text)}
    rules |= {f"value-of-{name}" for name, value in secrets.items() if value in text}
    return rules


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed")
    return result.stdout.decode("utf-8", errors="replace")


def scan_worktree(repo: Path, secrets: dict[str, str]) -> Iterable[Finding]:
    """Everything git could commit: tracked files, plus untracked files that aren't ignored."""
    for relative in _git(repo, "ls-files", "-co", "--exclude-standard").splitlines():
        path = repo / relative
        if not path.is_file():
            continue
        try:
            text = path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for rule in sorted(scan_text(line, secrets)):
                yield Finding("worktree", f"{relative}:{line_number}", rule)


def scan_history(repo: Path, secrets: dict[str, str]) -> Iterable[Finding]:
    """Every commit on every ref: messages, and all added or removed lines."""
    has_commits = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"], capture_output=True, check=False
    )
    if has_commits.returncode != 0:
        return
    log = _git(repo, "log", "--all", "-p", "-U0", "--no-color", "--no-textconv", "--format=COMMIT %H%n%B")
    commit = "?"
    current = "?"
    for line in log.splitlines():
        if line.startswith("COMMIT "):
            commit = line.split()[1][:10]
            current = "(message)"
            continue
        if line.startswith("+++ b/"):
            current = line[6:]
            continue
        if line.startswith(("--- ", "+++ ", "diff --git ", "index ", "@@ ")):
            continue
        for rule in sorted(scan_text(line, secrets)):
            yield Finding("history", f"{commit} {current}", rule)


def tracked_env_files(repo: Path) -> Iterable[Finding]:
    """A real .env must never be tracked, whatever it contains."""
    for relative in _git(repo, "ls-files").splitlines():
        name = Path(relative).name
        if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
            yield Finding("worktree", relative, "tracked-env-file")
    for relative in _git(repo, "log", "--all", "--format=", "--name-only", "--diff-filter=A").splitlines():
        name = Path(relative).name
        if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
            yield Finding("history", relative, "env-file-in-history")


def scan(repo: Path) -> list[Finding]:
    secrets = env_secrets(repo / ".env")
    findings = [*tracked_env_files(repo), *scan_worktree(repo, secrets), *scan_history(repo, secrets)]
    return sorted(set(findings), key=str)


def main(argv: list[str]) -> int:
    repo = Path(argv[0]).resolve() if argv else Path(__file__).resolve().parents[1]
    try:
        findings = scan(repo)
    except (RuntimeError, OSError) as error:
        print(f"secret scan could not run: {error}", file=sys.stderr)
        return 2
    if findings:
        print(f"secret scan FAILED: {len(findings)} finding(s). Matched values are not shown.")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print("secret scan clean: working tree and full history.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
