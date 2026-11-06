"""Measure matching on real public skills, at pinned commits. Reads public GitHub. Needs no LLM key.

usage: python run_real.py <output.json> <split> [threshold]

With no threshold it sweeps, for tuning. With one, it reports that split once, as a result.
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import yaml
from skill_customs import GitHubClient, LicenseRejected, Lockfile, pin

from try_professional.memory import FastEmbedEmbedder
from try_professional.org import load_org
from try_professional.packs import available_packs, install_org
from try_professional.skills import Decisions, SkillCatalog, compute_loadouts, records_from_lock

HERE = Path(__file__).parent


def no_usage(role: str, skill_id: str) -> dict[str, int]:
    return {"uses": 0, "passed": 0, "failed": 0, "idle_runs": 0}


def main(output: Path, split: str, threshold: float | None) -> None:
    labelled = yaml.safe_load((HERE / "real_skills.yaml").read_text(encoding="utf-8"))[split]
    work = Path(tempfile.mkdtemp(prefix="trypro-real-"))
    install_org(work / "org", "Real", available_packs())
    org = load_org(work / "org")
    lock = Lockfile(work / "skills.lock")
    client = GitHubClient()
    labels: dict[str, list[str]] = {}
    categories: dict[str, str] = {}
    for item in labelled:
        try:
            result = pin(item["repo"], item["commit"], path=item["path"], dest=work / "skills", client=client)
        except LicenseRejected:
            continue
        lock.add(result.entry)
        labels[result.entry.skill_id] = item["roles"]
        categories[result.entry.skill_id] = item["category"]
    # Treat them as cleared by a person, with the category that person would assign. Tools are
    # set aside, so this measures matching alone. Real runs would exclude skills needing a shell.
    records = [
        replace(r, trust="indexed", category=categories[r.skill_id], tools=())
        for r in records_from_lock(lock, work / "skills")
    ]
    catalog = SkillCatalog(work / "c.sqlite", FastEmbedEmbedder())
    catalog.rebuild(records)

    def measure(t: float) -> dict:
        loadouts = compute_loadouts(
            org, catalog.eligible(), catalog.embedder, Decisions(work / "n.yaml"), no_usage, threshold=t
        )
        correct, wrong, missed, rows = 0, 0, 0, []
        for role, loadout in sorted(loadouts.items()):
            got = {m.record.skill_id: m for m in loadout.pending[: org.role(role).max_skills]}
            collapsed = set(loadout.collapsed)
            wanted = {s for s, roles in labels.items() if role in roles}
            for skill, match in got.items():
                ok = skill in wanted
                correct += ok
                wrong += not ok
                rows.append({"role": role, "skill": skill.rsplit("/", 1)[-1], "score": round(match.score, 3), "right": ok})
            missed += len(wanted - set(got) - collapsed)
        precision = correct / (correct + wrong) if correct + wrong else 0.0
        recall = correct / (correct + missed) if correct + missed else 0.0
        return {"t": t, "precision": round(precision, 3), "recall": round(recall, 3), "correct": correct, "wrong": wrong, "missed": missed, "rows": rows}

    if threshold is None:
        report = {"split": split, "skills": len(labels), "sweep": [measure(round(0.30 + 0.02 * n, 2)) for n in range(21)]}
    else:
        report = {"split": split, "skills": len(labels), "result": measure(threshold)}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    catalog.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else None)
