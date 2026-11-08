"""Precision and recall per rule, and the verdict per case, on the labelled synthetic set. Free.

usage: python measure.py <skill_customs_src_dir> <label> <results.json>

Point it at a copy of the scanner's source to measure that version, so 0.1.0 and the fix are measured the same way.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CASES_FILE = HERE.parents[2] / "packages" / "skill-customs" / "tests" / "scanner_cases.py"


def main(source: Path, label: str, out: Path) -> None:
    sys.path.insert(0, str(source))
    from skill_customs import scan

    spec = importlib.util.spec_from_file_location("scanner_cases", CASES_FILE)
    assert spec and spec.loader
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = []
    with tempfile.TemporaryDirectory() as work:
        for case in module.CASES:
            folder = Path(work) / str(case["id"])
            folder.mkdir()
            (folder / "SKILL.md").write_text(str(case["text"]), encoding="utf-8")
            report = scan(folder)
            fired = sorted({f.rule for f in report.findings if f.severity in ("block", "review")})
            rows.append({"id": case["id"], "pattern": case["pattern"], "kind": case["kind"],
                         "expected_verdict": case["verdict"], "verdict": report.verdict,
                         "expected_rules": sorted(case["rules"]), "fired": fired})  # fmt: skip
    rules = sorted({r for row in rows for r in (*row["fired"], *row["expected_rules"])})
    per_rule = {}
    for rule in rules:
        tp = sum(rule in row["fired"] and rule in row["expected_rules"] for row in rows)
        fp = sum(rule in row["fired"] and rule not in row["expected_rules"] for row in rows)
        fn = sum(rule not in row["fired"] and rule in row["expected_rules"] for row in rows)
        per_rule[rule] = {
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": round(tp / (tp + fp), 2) if tp + fp else None,
            "recall": round(tp / (tp + fn), 2) if tp + fn else None,
        }
    right = [row for row in rows if row["verdict"] == row["expected_verdict"]]
    data = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
    data[label] = {"verdicts_right": len(right), "cases": len(rows), "per_rule": per_rule, "cases_detail": rows}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{label}: {len(right)} of {len(rows)} verdicts right")
    for row in rows:
        if row["verdict"] != row["expected_verdict"]:
            print(f"  {row['id']}: got {row['verdict']}, expected {row['expected_verdict']}, fired {row['fired']}")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(), sys.argv[2], Path(sys.argv[3]).resolve())
