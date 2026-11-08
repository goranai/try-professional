"""Scan a skill library, and compare the verdicts with an earlier scan of it. Prints totals and transitions only.

usage: python compare_library.py <library_dir> <earlier_results.json> <new_results.json>

The earlier results are a JSON list of {"skill": <path under the library>, "verdict": ...}, as scan_all.py writes them.
Nothing from the library is printed: no names, and no excerpts. The new results file holds them, so keep it private.
"""

from __future__ import annotations

import collections
import json
import sys
import time
from pathlib import Path

from skill_customs import Policy, __version__, scan


def main(library: Path, earlier: Path, out: Path) -> None:
    before = {row["skill"]: row["verdict"] for row in json.loads(earlier.read_text(encoding="utf-8"))}
    folders = sorted({p.parent for p in library.rglob("SKILL.md")})
    policy = Policy.default()
    rows = []
    rules: collections.Counter[str] = collections.Counter()
    started = time.time()
    for folder in folders:
        report = scan(folder, policy=policy)
        findings = [{"rule": f.rule, "severity": f.severity, "file": f.file, "line": f.line} for f in report.findings]
        rules.update(f.rule for f in report.findings if f.severity in ("block", "review"))
        rows.append({"skill": folder.relative_to(library).as_posix(), "verdict": report.verdict, "findings": findings})
    elapsed = time.time() - started
    out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    after = {row["skill"]: row["verdict"] for row in rows}
    print(f"skill-customs {__version__}: {len(rows)} skills in {elapsed:.0f} s")
    print("verdicts:", dict(collections.Counter(after.values())))
    moved = collections.Counter(f"{before[s]} -> {after[s]}" for s in after if s in before)
    print("transitions:", dict(sorted(moved.items())))
    print("not in the earlier scan:", sum(s not in before for s in after))
    print("rules firing at block or review, by skill finding:", dict(rules.most_common()))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
