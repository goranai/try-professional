# ruff: noqa: E501
"""Turn the runs and their blind grades into the metrics DESIGN.md names. Free.

usage: python report.py <out> <results.json> [setups...]
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from goals import GOALS  # noqa: E402

SIZES = {"small": ("G2", "G6"), "fix": ("G5",), "large": ("G1", "G3", "G4")}


def wilson(hits: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    p = hits / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    spread = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return (round(max(0.0, centre - spread), 2), round(min(1.0, centre + spread), 2))


def load(out: Path) -> list[dict[str, Any]]:
    codes = json.loads((out / "codes.json").read_text(encoding="utf-8"))
    rows = []
    for run_dir in sorted((out / "runs").iterdir()):
        result_path = run_dir / "result.json"
        if run_dir.name.startswith("pilot") or not result_path.is_file():
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        grade_path = out / "grades" / f"{codes[run_dir.name]}.json"
        if not grade_path.is_file():
            continue
        grade = json.loads(grade_path.read_text(encoding="utf-8"))
        final = grade["final"]
        must = final["must"]
        should = {k: v for k, v in final["should"].items() if k != "H"}
        rows.append({
            "run": run_dir.name, "goal": result["goal"], "setup": result["setup"], "finished": result["finished"],
            "passed": bool(result["finished"] and all(must.values())), "must_met": sum(must.values()),
            "must_total": len(must), "should_met": sum(should.values()), "should_total": len(should),
            "honest": bool(final["should"].get("H")), "holistic": final["holistic"],
            "cost_usd": result.get("cost_usd", 0.0), "seconds": result.get("seconds", 0),
            "questions": result.get("questions", []), "lines_stripped": grade.get("lines_stripped", 0),
            "legal_reviewed": result.get("legal_reviewed"), "first_two_agreed": grade.get("first_two_agreed"),
            "usage": result.get("usage", {}),
        })  # fmt: skip
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"runs": 0}
    passed = sum(r["passed"] for r in rows)
    finished = sum(r["finished"] for r in rows)
    honest = sum(r["honest"] for r in rows)
    cost = sum(r["cost_usd"] for r in rows)
    kinds: dict[str, int] = {}
    for r in rows:
        for q in r["questions"]:
            kind = "stuck" if q.get("native_kind") == "stuck" else q.get("kind", "other")
            kinds[kind] = kinds.get(kind, 0) + 1
    tokens = {k: sum(int(r["usage"].get(k, 0)) for r in rows) for k in ("input", "cache_write", "cache_read", "output")}
    return {
        "runs": n,
        "pass_rate": round(passed / n, 3),
        "pass_ci95": wilson(passed, n),
        "passed": passed,
        "finish_rate": round(finished / n, 3),
        "must_met": round(sum(r["must_met"] for r in rows) / sum(r["must_total"] for r in rows), 3),
        "should_met": round(sum(r["should_met"] for r in rows) / max(1, sum(r["should_total"] for r in rows)), 3),
        "honesty": round(honest / n, 3),
        "holistic_mean": round(statistics.mean(r["holistic"] for r in rows), 2),
        "holistic_range": [min(r["holistic"] for r in rows), max(r["holistic"] for r in rows)],
        "cost_usd": round(cost, 2),
        "cost_per_run": round(cost / n, 3),
        "cost_per_pass": round(cost / passed, 3) if passed else None,
        "minutes_per_run": round(sum(r["seconds"] for r in rows) / n / 60, 1),
        "questions_per_run": {k: round(v / n, 2) for k, v in sorted(kinds.items())},
        "lines_stripped_per_run": round(sum(r["lines_stripped"] for r in rows) / n, 1),
        "tokens": tokens,
    }


def winners(by_goal: dict[str, dict[str, Any]], setups: list[str]) -> dict[str, str]:
    """DESIGN.md's rule: more passing runs wins; on a tie, a mean holistic score higher by at least 0.5."""
    out = {}
    for goal, cells in by_goal.items():
        ranked = sorted(
            setups, key=lambda s: (cells[s].get("passed", 0), cells[s].get("holistic_mean", 0)), reverse=True
        )
        best, second = ranked[0], ranked[1]
        a, b = cells[best], cells[second]
        if a.get("passed", 0) > b.get("passed", 0) or a.get("holistic_mean", 0) - b.get("holistic_mean", 0) >= 0.5:
            out[goal] = best
        else:
            out[goal] = "tie"
    return out


def main(out: Path, target: Path, setups: list[str]) -> None:
    rows = [r for r in load(out) if r["setup"] in setups]
    report: dict[str, Any] = {"setups": setups, "runs": len(rows)}
    report["overall"] = {s: summarize([r for r in rows if r["setup"] == s]) for s in setups}
    report["by_size"] = {
        size: {s: summarize([r for r in rows if r["setup"] == s and r["goal"] in goals]) for s in setups}
        for size, goals in SIZES.items()
    }
    report["by_goal"] = {g: {s: summarize([r for r in rows if r["setup"] == s and r["goal"] == g]) for s in setups}
                         for g in GOALS}  # fmt: skip
    report["wins_by_goal"] = winners(report["by_goal"], setups)
    hierarchy = [r for r in rows if r["setup"].startswith("hierarchy")]
    report["legal_reviewed_runs"] = sorted(r["run"] for r in hierarchy if r["legal_reviewed"])
    agreed = [r["first_two_agreed"] for r in rows if r["first_two_agreed"] is not None]
    report["grader_first_two_agreed"] = round(sum(agreed) / len(agreed), 3) if agreed else None
    report["grader_agreement_by_goal"] = {
        g: round(sum(r["first_two_agreed"] for r in rows if r["goal"] == g) / max(1, sum(r["goal"] == g for r in rows)), 2)
        for g in GOALS
    }  # fmt: skip
    report["rows"] = rows
    target.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for s in setups:
        o = report["overall"][s]
        print(f"{s:16} pass {o.get('passed')}/{o.get('runs')} must {o.get('must_met')} finish {o.get('finish_rate')}"
              f" holistic {o.get('holistic_mean')} ${o.get('cost_per_run')}/run {o.get('minutes_per_run')} min")  # fmt: skip
    print("wins by goal:", report["wins_by_goal"])


if __name__ == "__main__":
    chosen = sys.argv[3:] or ["one-agent", "flat-swarm", "hierarchy"]
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), chosen)
