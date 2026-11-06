"""Compare multilingual-e5-large with the default model, on the same labelled queries.

usage: python compare_e5.py <output.json> <model cache dir>

Same rule as run_retrieval.py: the floor comes from the dev split only. e5 expects
"query: " and "passage: " prefixes.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np

MARGIN = 0.02
MODELS = {
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": ("", ""),
    "intfloat/multilingual-e5-large": ("query: ", "passage: "),
}


def main(output: Path, cache: Path) -> None:
    import psutil
    from fastembed import TextEmbedding

    from try_professional.bench.retrieval.evaluate import (
        correct_hit_scores,
        evaluate,
        load_catalog,
        nonsense_top_scores,
    )

    skills = load_catalog()
    ids = [s["id"] for s in skills]
    report = {}
    for name, (query_prefix, passage_prefix) in MODELS.items():
        started = time.time()
        rss_before = psutil.Process(os.getpid()).memory_info().rss
        model = TextEmbedding(model_name=name, cache_dir=str(cache))
        docs = np.array(list(model.embed([f"{passage_prefix}{s['id']}: {s['description']}" for s in skills])))
        docs /= np.linalg.norm(docs, axis=1, keepdims=True)
        memo: dict[str, list[tuple[str, float]]] = {}

        def rank(query: str) -> list[tuple[str, float]]:
            if query not in memo:
                vector = np.array(list(model.embed([f"{query_prefix}{query}"])))[0]
                vector /= np.linalg.norm(vector)
                scores = docs @ vector
                memo[query] = [(ids[i], float(scores[i])) for i in np.argsort(-scores)[:5]]
            return memo[query]

        dev_nonsense = nonsense_top_scores(rank, "dev")
        dev_correct = correct_hit_scores(rank, "dev")
        floor = round(max(dev_nonsense) + MARGIN, 3)
        heldout = evaluate(rank, "heldout", floor)
        no_floor = evaluate(rank, "heldout", 0.0)
        heldout_nonsense = nonsense_top_scores(rank, "heldout")
        report[name] = {
            "seconds_load_and_index": round(time.time() - started, 1),
            "rss_increase_gb": round((psutil.Process(os.getpid()).memory_info().rss - rss_before) / 1e9, 2),
            "dev_nonsense_top_max": round(max(dev_nonsense), 3),
            "dev_correct_min": round(min(dev_correct), 3),
            "chosen_floor": floor,
            "heldout_recall_at_5": {k: f"{v.top5}/{v.total}" for k, v in heldout.items()},
            "heldout_total": f"{sum(v.top5 for v in heldout.values())}/100",
            "heldout_recall_at_5_no_floor": {k: f"{v.top5}/{v.total}" for k, v in no_floor.items()},
            "heldout_total_no_floor": f"{sum(v.top5 for v in no_floor.values())}/100",
            "heldout_top_1": f"{sum(v.top1 for v in no_floor.values())}/100",
            "nonsense_rejected": f"{sum(s < floor for s in heldout_nonsense)}/{len(heldout_nonsense)}",
            "nonsense_max": round(max(heldout_nonsense), 3),
            "correct_min_heldout": round(min(correct_hit_scores(rank, "heldout")), 3),
        }
        del model
        output.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
