"""The skill-customs command. Prints only sanitized text, because skill content is untrusted."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import yaml

from . import __version__
from .attribution import render_attribution
from .discover import discover
from .errors import SkillCustomsError
from .fetch import GitHubClient
from .install import approve, pin, pin_local, remove, skill_dir, verify
from .licenses import check_license
from .lockfile import Lockfile
from .policy import Policy
from .scan import ScanReport, decide_trust, scan


def _out(text: str = "") -> None:
    stream = sys.stdout
    encoding = stream.encoding or "utf-8"
    stream.write(text.encode(encoding, errors="backslashreplace").decode(encoding) + "\n")


def _print_report(report: ScanReport) -> None:
    for finding in report.findings:
        where = f"{finding.file}:{finding.line}" if finding.line else finding.file
        _out(f"  {finding.severity:<6} {where}  [{finding.rule}] {finding.message}")
        if finding.excerpt:
            _out(f"         {finding.excerpt}")


def cmd_scan(args: argparse.Namespace, policy: Policy) -> int:
    report = scan(Path(args.path), policy)
    if args.json:
        _out(
            json.dumps(
                {
                    "verdict": report.verdict,
                    "trust": decide_trust(report),
                    "executable_files": report.executable_files,
                    "findings": [{**f.to_dict(), "message": f.message} for f in report.findings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        _print_report(report)
        counts = f"{report.files_scanned} files, {len(report.findings)} findings"
        _out(f"{report.verdict}: {counts}. Would be: {decide_trust(report)}.")
        if report.verdict == "pass":
            _out("Pass means no known signal was found. It doesn't mean this skill is safe.")
    return report.exit_code


def cmd_license(args: argparse.Namespace, policy: Policy) -> int:
    verdict = check_license(Path(args.path), policy, repo_root=Path(args.repo_root) if args.repo_root else None)
    _out(f"{'allowed' if verdict.allowed else 'rejected'}: {verdict.spdx or 'unknown'}. {verdict.reason}")
    _out(f"Commercial use: {verdict.commercial_use}. Evidence: {verdict.evidence}.")
    return 0 if verdict.allowed else 1


def cmd_resolve(args: argparse.Namespace, policy: Policy) -> int:
    repo, _, ref = args.target.partition("@")
    _out(GitHubClient().resolve_ref(repo, ref or "HEAD"))
    return 0


def cmd_add(args: argparse.Namespace, policy: Policy) -> int:
    lock = Lockfile.load(args.lock)
    result = pin(args.repo, args.commit, path=args.path, dest=Path(args.dest), policy=policy)
    lock.add(result.entry).save()
    _print_report(result.report)
    _out(f"{result.entry.skill_id}: {result.entry.trust} ({result.license.spdx})")
    _out(f"Location: {skill_dir(result.entry, Path(args.dest))}")
    if result.entry.trust == "quarantined":
        _out(f"A person must review it, then run: skill-customs approve {result.entry.skill_id} --by <your name>")
    return 0


def cmd_add_local(args: argparse.Namespace, policy: Policy) -> int:
    lock = Lockfile.load(args.lock)
    result = pin_local(Path(args.path), license_spdx=args.license, dest=Path(args.dest), policy=policy)
    lock.add(result.entry).save()
    _print_report(result.report)
    _out(f"{result.entry.skill_id}: quarantined until a person approves it.")
    return 0


def cmd_approve(args: argparse.Namespace, policy: Policy) -> int:
    """People only. It refuses to run without a terminal, and asks for the name to be typed back."""
    if not sys.stdin.isatty():
        _out("approve needs a person at a terminal, so it won't run from a script or an agent.")
        return 2
    lock = Lockfile.load(args.lock)
    entry = lock.get(args.skill_id)
    _out(f"Approving {entry.skill_id}: scan verdict {entry.scan.get('verdict')}, license {entry.license_spdx}.")
    _out(f"Review the files in {skill_dir(entry, Path(args.dest))} first.")
    if input("Type the skill ID to confirm: ").strip() != args.skill_id:
        _out("It didn't match, so nothing was approved.")
        return 1
    approved = approve(
        lock, args.skill_id, by=args.by, dest=Path(args.dest), accept_blocking_findings=args.accept_findings
    )
    lock.save()
    _out(f"{approved.skill_id}: verified by {args.by}. Any later change voids this.")
    return 0


def cmd_verify(args: argparse.Namespace, policy: Policy) -> int:
    lock = Lockfile.load(args.lock)
    problems = verify(lock, dest=Path(args.dest), policy=policy)
    for problem in problems:
        _out(f"  {problem}")
    if problems:
        _out(f"Failed: {len(problems)} problem(s).")
        return 1
    _out(f"OK: {len(lock.entries)} skill(s) match {args.lock}.")
    return 0


def cmd_list(args: argparse.Namespace, policy: Policy) -> int:
    lock = Lockfile.load(args.lock)
    if not lock.entries:
        _out("No skills are locked.")
    for skill_id, entry in sorted(lock.entries.items()):
        commit = entry.commit[:12] or "local"
        _out(f"{entry.trust:<12} {entry.license_spdx or 'unknown':<14} {commit:<13} {skill_id}")
    return 0


def cmd_remove(args: argparse.Namespace, policy: Policy) -> int:
    lock = Lockfile.load(args.lock)
    remove(lock, args.skill_id, dest=Path(args.dest))
    lock.save()
    _out(f"Removed {args.skill_id}.")
    return 0


def cmd_attribution(args: argparse.Namespace, policy: Policy) -> int:
    text = render_attribution(Lockfile.load(args.lock))
    if args.check:
        target = Path(args.check)
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        if current != text:
            _out(f"{target} is out of date. Regenerate it with: skill-customs attribution --out {target}")
            return 1
        _out(f"{target} is up to date.")
    elif args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        _out(text.rstrip())
    return 0


def cmd_discover(args: argparse.Namespace, policy: Policy) -> int:
    if args.categories:
        categories = yaml.safe_load(Path(args.categories).read_text(encoding="utf-8")) or {}
        results = {
            name: [c.to_dict() for c in discover(query, policy=policy, top=args.top)]
            for name, query in sorted(categories.items())
        }
        text = json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8", newline="\n")
            _out(
                f"Wrote {sum(len(v) for v in results.values())} candidates in {len(results)} categories to {args.out}."
            )
        else:
            _out(text.rstrip())
        return 0
    if not args.query:
        _out("Give a search query, or --categories with a file mapping each category to a query.")
        return 2
    found = discover(args.query, policy=policy, top=args.top)
    if args.json:
        _out(json.dumps([c.to_dict() for c in found], ensure_ascii=False, indent=2))
        return 0
    for candidate in found:
        flag = "" if candidate.license_allowed else "  [license not on the allowlist]"
        _out(f"{candidate.score:>7.2f}  {candidate.stars:>7}  {candidate.repo}  {candidate.license_spdx}{flag}")
    _out("These are candidates. Nothing was installed, and ranking says nothing about safety.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skill-customs", description=__doc__)
    parser.add_argument("--version", action="version", version=f"skill-customs {__version__}")
    parser.add_argument("--policy", help="Policy file. Default: skill-customs.yaml, if it exists")
    parser.add_argument("--lock", default="skills.lock")
    parser.add_argument("--dest", default="skills", help="Folder that holds installed/ and quarantine/")
    sub = parser.add_subparsers(dest="command", required=True)

    item = sub.add_parser("scan", help="Scan a skill folder. Exit 0 pass, 1 review, 2 block")
    item.add_argument("path")
    item.add_argument("--json", action="store_true")
    item.set_defaults(func=cmd_scan)

    item = sub.add_parser("license", help="Check a skill folder against the allowlist")
    item.add_argument("path")
    item.add_argument("--repo-root", help="Also read this folder's license file")
    item.set_defaults(func=cmd_license)

    item = sub.add_parser("resolve", help="Print the commit SHA for owner/repo@ref. Installs nothing")
    item.add_argument("target")
    item.set_defaults(func=cmd_resolve)

    item = sub.add_parser("add", help="Pin a skill from GitHub by full commit SHA")
    item.add_argument("repo")
    item.add_argument("--commit", required=True)
    item.add_argument("--path", default="", help="Skill folder inside the repository")
    item.set_defaults(func=cmd_add)

    item = sub.add_parser("add-local", help="Add a skill written here. Always quarantined")
    item.add_argument("path")
    item.add_argument("--license", required=True, help="SPDX ID you are releasing it under")
    item.set_defaults(func=cmd_add_local)

    item = sub.add_parser("approve", help="Approve a skill, as a person at a terminal")
    item.add_argument("skill_id")
    item.add_argument("--by", required=True)
    item.add_argument("--accept-findings", action="store_true", help="Approve despite blocking findings")
    item.set_defaults(func=cmd_approve)

    sub.add_parser("verify", help="Check installed skills against skills.lock").set_defaults(func=cmd_verify)
    sub.add_parser("list", help="List locked skills").set_defaults(func=cmd_list)

    item = sub.add_parser("remove", help="Remove a skill and its lock entry")
    item.add_argument("skill_id")
    item.set_defaults(func=cmd_remove)

    item = sub.add_parser("attribution", help="Generate THIRD_PARTY_SKILLS.md")
    item.add_argument("--out")
    item.add_argument("--check", help="Fail if this file isn't up to date")
    item.set_defaults(func=cmd_attribution)

    item = sub.add_parser("discover", help="Rank candidate repositories. Installs nothing")
    item.add_argument("query", nargs="?")
    item.add_argument("--categories", help="YAML file mapping each category to a search query")
    item.add_argument("--out", help="With --categories: write the JSON here")
    item.add_argument("--top", type=int, default=40, help="Keep this many per category")
    item.add_argument("--json", action="store_true")
    item.set_defaults(func=cmd_discover)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        policy = Policy.load(Path(args.policy) if args.policy else None)
        result: int = args.func(args, policy)
        return result
    except (SkillCustomsError, FileNotFoundError, ValueError) as error:
        _out(f"Error: {error}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
