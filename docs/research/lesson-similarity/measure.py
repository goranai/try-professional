"""Repeat the local measurement that separates paraphrases from distinct lessons."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from try_professional.memory.embedding import DEFAULT_MODEL, FastEmbedEmbedder


def main() -> None:
    source = Path(__file__).resolve().parents[3] / "packages/try-professional/tests/data/lesson_similarity.json"
    measured = json.loads(source.read_text(encoding="utf-8"))
    texts = list(measured["vectors"])
    vectors = FastEmbedEmbedder().embed_documents(texts)
    pairs = []
    for pair in measured["pairs"]:
        score = float(vectors[texts.index(pair["first"])] @ vectors[texts.index(pair["second"])])
        pairs.append({**pair, "score": score})
    print(
        json.dumps(
            {
                "model": DEFAULT_MODEL,
                "measured_at": datetime.now(timezone.utc).isoformat(),
                "fastembed_version": version("fastembed"),
                "pairs": pairs,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
