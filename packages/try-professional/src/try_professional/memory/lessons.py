"""Compare lessons without putting their evidence into the words being compared."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .embedding import DEFAULT_MODEL, Vectors

# Repeated lessons crowded out useful notes in 0.7.4. The measured paraphrases and
# related but distinct lessons in tests/data/lesson_similarity.json justify this
# document-to-document cosine threshold. Unmeasured models merge exact text only.
LESSON_SIMILARITY = {DEFAULT_MODEL: 0.95}


def closest_lesson(vector: Vectors, candidates: list[Vectors], model: str) -> int | None:
    threshold = LESSON_SIMILARITY.get(model)
    if threshold is None or not candidates:
        return None
    scores = np.asarray(candidates) @ vector
    best = int(np.argmax(scores))
    return best if float(scores[best]) >= threshold else None


@dataclass(frozen=True)
class Learning:
    source_run: str
    source: str
    proposed_by: str
    text: str
    at: str

    @property
    def reference(self) -> str:
        return f"{self.source_run}: {self.source}, learned by {self.proposed_by} at {self.at}"
