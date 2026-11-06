"""Choose the match threshold on the dev split, then report precision on the heldout split.

usage: python run_loadouts.py <output.json>      Needs no LLM key.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


def main(output: Path) -> None:
    from try_professional.bench.retrieval.loadout_eval import evaluate_tasks, fixture_records, score_loadouts
    from try_professional.memory import FastEmbedEmbedder
    from try_professional.org import load_org
    from try_professional.packs import available_packs, install_org
    from try_professional.skills import SkillCatalog

    work = Path(tempfile.mkdtemp(prefix="trypro-loadouts-"))
    install_org(work / "org", "Eval", available_packs())
    org = load_org(work / "org")
    catalog = SkillCatalog(work / "catalog.sqlite", FastEmbedEmbedder())
    catalog.rebuild(fixture_records())

    sweep = [round(0.30 + 0.02 * n, 2) for n in range(26)]
    dev = [score_loadouts(org, catalog, "dev", t, work) for t in sweep]
    best = max(dev, key=lambda s: (round(s.f1, 4), s.precision))
    heldout = score_loadouts(org, catalog, "heldout", best.threshold, work)

    def summary(score) -> dict:
        return {
            "threshold": score.threshold,
            "precision": round(score.precision, 3),
            "recall": round(score.recall, 3),
            "f1": round(score.f1, 3),
            "correct": score.true_positives,
            "wrong": score.false_positives,
            "missed": score.false_negatives,
            "by_language_found": score.by_language,
        }

    report = {
        "dev_sweep": [{"t": s.threshold, "p": round(s.precision, 3), "r": round(s.recall, 3)} for s in dev],
        "chosen_on_dev": summary(best),
        "heldout": summary(heldout),
        "heldout_per_role": heldout.per_role,
    }

    # Task queries, searched only within what matching gave that role, as a real run would.
    report["tasks"] = evaluate_tasks(org, catalog, best.threshold, work)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    catalog.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]))
