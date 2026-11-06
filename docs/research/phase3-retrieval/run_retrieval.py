"""Measure skill retrieval and choose the score floor. Needs no LLM key.

usage: python run_retrieval.py <output.json>

The floor is chosen on the dev split only: the highest nonsense score, plus a small
margin. The heldout split is scored once with that floor, so it stays an honest estimate.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

MARGIN = 0.02
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def main(output: Path) -> None:
    from try_professional.bench.retrieval.evaluate import (
        correct_hit_scores,
        evaluate,
        load_catalog,
        nonsense_top_scores,
    )
    from try_professional.memory import FastEmbedEmbedder, MemorySettings, MnemosyneBackend
    from try_professional.skills import SkillCatalog, SkillRecord

    work = Path(tempfile.mkdtemp(prefix="trypro-retrieval-"))
    report: dict = {"model": MODEL, "skills": len(load_catalog()), "margin": MARGIN}

    embedder = FastEmbedEmbedder(MODEL)
    started = time.time()
    catalog = SkillCatalog(work / "catalog.sqlite", embedder)
    catalog.rebuild(
        [
            SkillRecord(skill_id=s["id"], name=s["id"], description=s["description"], category="benchmark")
            for s in load_catalog()
        ]
    )
    report["catalog_build_seconds"] = round(time.time() - started, 1)

    def rank_catalog(query: str) -> list[tuple[str, float]]:
        return [(h.record.skill_id, h.score) for h in catalog.search(query, top_k=5)]

    settings = MemorySettings(data_dir=work / "memory")
    backend = MnemosyneBackend(settings)
    for skill in load_catalog():
        backend.remember("skills", f"{skill['id']}: {skill['description']}", source="benchmark")

    def rank_mnemosyne(query: str) -> list[tuple[str, float]]:
        return [(h.text.split(":", 1)[0], h.dense_score) for h in backend.recall("skills", query, 5)]

    for name, rank in (("catalog_cosine", rank_catalog), ("mnemosyne_gate0", rank_mnemosyne)):
        dev_nonsense = nonsense_top_scores(rank, "dev")
        dev_correct = correct_hit_scores(rank, "dev")
        floor = round(max(dev_nonsense) + MARGIN, 3)
        heldout = evaluate(rank, "heldout", floor)
        heldout_nonsense = nonsense_top_scores(rank, "heldout")
        report[name] = {
            "dev": {
                "nonsense_top_max": round(max(dev_nonsense), 3),
                "correct_min": round(min(dev_correct), 3),
                "correct_median": round(statistics.median(dev_correct), 3),
                "recall_at_5_no_floor": {k: f"{v.top5}/{v.total}" for k, v in evaluate(rank, "dev").items()},
            },
            "chosen_floor": floor,
            "heldout": {
                "recall_at_5": {k: f"{v.top5}/{v.total}" for k, v in heldout.items()},
                "top_1": {k: f"{v.top1}/{v.total}" for k, v in heldout.items()},
                "total_recall_at_5": f"{sum(v.top5 for v in heldout.values())}/{sum(v.total for v in heldout.values())}",
                "review_status": {k: v.review_status for k, v in heldout.items()},
                "misses": [m for v in heldout.values() for m in v.misses],
                "nonsense_scores": [round(s, 3) for s in heldout_nonsense],
                "nonsense_rejected": f"{sum(s < floor for s in heldout_nonsense)}/{len(heldout_nonsense)}",
            },
        }

    try:
        import psutil

        report["process_rss_gb_with_both_models_loaded"] = round(psutil.Process(os.getpid()).memory_info().rss / 1e9, 2)
    except ImportError:
        report["process_rss_gb_with_both_models_loaded"] = "psutil not installed"

    output.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    catalog.close()


if __name__ == "__main__":
    main(Path(sys.argv[1]))
