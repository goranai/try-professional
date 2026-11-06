"""Skill-retrieval benchmark: recall@5 for the same skills, queried in five languages.

Needs no LLM key. Thresholds are chosen on the dev split and only reported on heldout,
so the heldout number stays an honest estimate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

import yaml

LANGUAGES = ("en", "id", "es", "zh", "ar")
Ranker = Callable[[str], list[tuple[str, float]]]


def _load(name: str) -> dict[str, Any]:
    text = resources.files("try_professional.bench.retrieval").joinpath(name).read_text(encoding="utf-8")
    loaded: dict[str, Any] = yaml.safe_load(text)
    return loaded


def load_catalog() -> list[dict[str, str]]:
    skills: list[dict[str, str]] = _load("skills.yaml")["skills"]
    return skills


def load_queries(language: str) -> dict[str, Any]:
    return _load(f"queries/{language}.yaml")


def load_nonsense() -> list[dict[str, str]]:
    queries: list[dict[str, str]] = _load("nonsense.yaml")["queries"]
    return queries


@dataclass
class LanguageResult:
    language: str
    review_status: str
    total: int = 0
    top1: int = 0
    top5: int = 0
    misses: list[dict[str, Any]] = field(default_factory=list)

    @property
    def recall_at_5(self) -> float:
        return self.top5 / self.total if self.total else 0.0


def evaluate(rank: Ranker, split: str, floor: float = 0.0) -> dict[str, LanguageResult]:
    results = {}
    for language in LANGUAGES:
        document = load_queries(language)
        result = LanguageResult(language, document["review"]["status"])
        for query in document["queries"]:
            if query["split"] != split:
                continue
            ranked = [skill for skill, score in rank(query["text"]) if score >= floor][:5]
            result.total += 1
            result.top1 += int(ranked[:1] == [query["skill"]])
            if query["skill"] in ranked:
                result.top5 += 1
            else:
                result.misses.append({"want": query["skill"], "text": query["text"], "got": ranked[:3]})
        results[language] = result
    return results


def nonsense_top_scores(rank: Ranker, split: str) -> list[float]:
    """The best score each nonsense query gets. These should all fall below the floor."""
    scores = []
    for query in load_nonsense():
        if query["split"] == split:
            ranked = rank(query["text"])
            scores.append(max((score for _, score in ranked), default=0.0))
    return scores


def correct_hit_scores(rank: Ranker, split: str) -> list[float]:
    """The score of the right skill, whenever it reaches the top 5."""
    scores = []
    for language in LANGUAGES:
        for query in load_queries(language)["queries"]:
            if query["split"] == split:
                scores += [score for skill, score in rank(query["text"])[:5] if skill == query["skill"]]
    return scores
