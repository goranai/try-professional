"""Pin, verify, and approve skills.

Trusted skills go in <dest>/installed, and quarantined ones in <dest>/quarantine, so pointing
a framework at the installed folder can't pick up something unapproved.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from .errors import ApprovalError, FetchError, LicenseRejected, NotASkill
from .fetch import GitHubClient, check_repo, extract_skill, is_full_sha
from .frontmatter import SKILL_FILE, read_skill_metadata
from .hashing import content_hash
from .licenses import LicenseVerdict, check_license
from .lockfile import Approval, LockEntry, Lockfile
from .policy import Policy
from .scan import RULESET, SCANNER_VERSION, Scanner, ScanReport, decide_trust, scan

INSTALLED = "installed"
QUARANTINE = "quarantine"
_LOCAL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass
class PinResult:
    entry: LockEntry
    report: ScanReport
    license: LicenseVerdict


def skill_dir(entry: LockEntry, dest: Path) -> Path:
    """Where this skill lives. Quarantined skills are never in the installed folder."""
    area = QUARANTINE if entry.trust == "quarantined" else INSTALLED
    return dest.joinpath(area, *entry.skill_id.split("/"))


def _scan_summary(report: ScanReport) -> dict[str, object]:
    return {
        "executable_files": report.executable_files,
        "findings": [f.to_dict() for f in report.findings],
        "ruleset": RULESET,
        "scanner_version": SCANNER_VERSION,
        "scanners": report.scanners,
        "verdict": report.verdict,
    }


def _place(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))


def _remove_other_copy(entry: LockEntry, dest: Path) -> None:
    for area in (INSTALLED, QUARANTINE):
        other = dest.joinpath(area, *entry.skill_id.split("/"))
        if other != skill_dir(entry, dest) and other.exists():
            shutil.rmtree(other)


def pin(
    repo: str,
    commit: str,
    *,
    path: str = "",
    dest: Path = Path("skills"),
    policy: Policy | None = None,
    client: GitHubClient | None = None,
    scanners: list[Scanner] | None = None,
) -> PinResult:
    """Fetch one skill at one commit, check it, and place it. The caller adds the entry to a Lockfile.

    A rejected license installs nothing. A clean skill becomes indexed, and anything else
    is quarantined until a person approves it. Nothing from the skill is run at any point.
    """
    active = policy or Policy.default()
    check_repo(repo)
    if not is_full_sha(commit):
        raise FetchError(f"'{commit}' isn't a full 40-character commit SHA. Use 'resolve' to get one.")
    github = client or GitHubClient()
    with tempfile.TemporaryDirectory(prefix="skill-customs-") as work:
        root = Path(work)
        archive = root / "archive.tar.gz"
        github.download_tarball(repo, commit, archive, active)
        extracted = root / "skill"
        repo_root = extract_skill(archive, path, commit, extracted, active)
        if not (extracted / SKILL_FILE).is_file():
            raise NotASkill(f"{repo}/{path or '.'} has no {SKILL_FILE} at this commit")
        verdict = check_license(extracted, active, repo_root=repo_root)
        if not verdict.allowed:
            raise LicenseRejected(f"{repo}/{path or '.'}: {verdict.reason}", verdict)
        report = scan(extracted, active, scanners=scanners)
        skill_id = "/".join(part for part in (repo, path.strip("/")) if part)
        entry = LockEntry(
            skill_id=skill_id,
            host="github.com",
            repo=repo,
            path=path.strip("/"),
            commit=commit,
            content_sha256=content_hash(extracted),
            license_spdx=verdict.spdx,
            license_evidence=verdict.evidence,
            commercial_use=verdict.commercial_use,
            trust=decide_trust(report),
            scan=_scan_summary(report),
        )
        _remove_other_copy(entry, dest)
        _place(extracted, skill_dir(entry, dest))
    return PinResult(entry, report, verdict)


def pin_local(
    source: Path,
    *,
    license_spdx: str,
    dest: Path = Path("skills"),
    policy: Policy | None = None,
    scanners: list[Scanner] | None = None,
) -> PinResult:
    """Add a skill written here, such as one an agent proposed. Always quarantined until approved."""
    active = policy or Policy.default()
    read_skill_metadata(source)
    if not _LOCAL_NAME.match(source.resolve().name):
        raise NotASkill(f"'{source.name}' can't be used as a skill name")
    if license_spdx not in active.allowed_licenses:
        verdict = LicenseVerdict(license_spdx, False, "unknown", "declared", "It isn't on the allowlist.")
        raise LicenseRejected(f"{license_spdx} isn't on the allowlist", verdict)
    verdict = LicenseVerdict(license_spdx, True, "allowed", "declared by the author", "On the allowlist.")
    report = scan(source, active, scanners=scanners)
    entry = LockEntry(
        skill_id=f"local/{source.resolve().name}",
        host="local",
        repo="",
        path="",
        commit="",
        content_sha256=content_hash(source),
        license_spdx=license_spdx,
        license_evidence=verdict.evidence,
        commercial_use="allowed",
        trust="quarantined",
        scan=_scan_summary(report),
    )
    target = skill_dir(entry, dest)
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, symlinks=True)
    return PinResult(entry, report, verdict)


def approve(
    lock: Lockfile,
    skill_id: str,
    *,
    by: str,
    dest: Path = Path("skills"),
    accept_blocking_findings: bool = False,
) -> LockEntry:
    """Record a person's approval, tied to this exact content. Any later change voids it.

    This is deliberately a command for people. Don't expose it as an agent tool.
    """
    if not by.strip():
        raise ApprovalError("An approval needs a name")
    entry = lock.get(skill_id)
    current = skill_dir(entry, dest)
    if not current.is_dir():
        raise ApprovalError(f"{current} doesn't exist")
    actual = content_hash(current)
    if actual != entry.content_sha256:
        raise ApprovalError(f"'{skill_id}' changed since it was scanned. Add it again first.")
    if entry.scan.get("verdict") == "block" and not accept_blocking_findings:
        raise ApprovalError(f"'{skill_id}' has blocking findings. Review them, then pass the accept option.")
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    approved = replace(entry, trust="verified", approval=Approval(by.strip(), stamp, actual))
    target = skill_dir(approved, dest)
    if target != current:
        _place(current, target)
    lock.add(approved)
    return approved


def remove(lock: Lockfile, skill_id: str, *, dest: Path = Path("skills")) -> None:
    entry = lock.get(skill_id)
    for area in (INSTALLED, QUARANTINE):
        folder = dest.joinpath(area, *entry.skill_id.split("/"))
        if folder.exists():
            shutil.rmtree(folder)
    del lock.entries[skill_id]


def _skill_folders(area: Path) -> list[Path]:
    return sorted(p.parent for p in area.rglob(SKILL_FILE)) if area.is_dir() else []


def verify(lock: Lockfile, *, dest: Path = Path("skills"), policy: Policy | None = None) -> list[str]:
    """Return every way the installed skills differ from skills.lock. Empty means they match."""
    active = policy or Policy.default()
    problems: list[str] = []
    expected: set[Path] = set()
    for skill_id, entry in sorted(lock.entries.items()):
        folder = skill_dir(entry, dest)
        expected.add(folder.resolve())
        if entry.host != "local" and not is_full_sha(entry.commit):
            problems.append(f"{skill_id}: not pinned to a full commit SHA")
        if entry.license_spdx not in active.allowed_licenses:
            problems.append(f"{skill_id}: license {entry.license_spdx} isn't on the allowlist")
        if entry.trust == "indexed" and entry.scan.get("verdict") != "pass":
            problems.append(f"{skill_id}: indexed despite scan findings")
        if not folder.is_dir():
            problems.append(f"{skill_id}: missing from {folder}")
            continue
        actual = content_hash(folder)
        if actual != entry.content_sha256:
            problems.append(f"{skill_id}: content doesn't match the lockfile")
        if entry.trust == "verified":
            if entry.approval is None:
                problems.append(f"{skill_id}: verified with no approval recorded")
            elif entry.approval.content_sha256 != actual:
                problems.append(f"{skill_id}: changed since it was approved")
        elif entry.approval is not None:
            problems.append(f"{skill_id}: has an approval but isn't verified")
    for area in (INSTALLED, QUARANTINE):
        for folder in _skill_folders(dest / area):
            if folder.resolve() not in expected:
                problems.append(f"{folder}: not in the lockfile")
    return problems


__all__ = ["PinResult", "approve", "pin", "pin_local", "remove", "skill_dir", "verify"]
