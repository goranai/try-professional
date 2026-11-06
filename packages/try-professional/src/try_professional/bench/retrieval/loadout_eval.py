"""Measure how well loadouts fit roles, against a hand-labelled fixture. Needs no LLM key."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from ...org import Org
from ...skills import Decisions, SkillRecord, compute_loadouts


def load_fixture() -> dict[str, Any]:
    text = (
        resources.files("try_professional.bench.retrieval").joinpath("loadout_fixture.yaml").read_text(encoding="utf-8")
    )
    loaded: dict[str, Any] = yaml.safe_load(text)
    return loaded


def fixture_records(split: str | None = None) -> list[SkillRecord]:
    return [
        SkillRecord(
            skill_id=s["id"], name=s["id"], description=s["description"], category=s["category"], content_sha256=s["id"]
        )
        for s in load_fixture()["skills"]
        if split is None or s["split"] == split
    ]


@dataclass
class LoadoutScore:
    threshold: float
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    per_role: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_language: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def precision(self) -> float:
        total = self.true_positives + self.false_positives
        return self.true_positives / total if total else 0.0

    @property
    def recall(self) -> float:
        total = self.true_positives + self.false_negatives
        return self.true_positives / total if total else 0.0

    @property
    def f1(self) -> float:
        total = self.precision + self.recall
        return 2 * self.precision * self.recall / total if total else 0.0


def score_loadouts(org: Org, catalog: Any, split: str, threshold: float, work: Path) -> LoadoutScore:
    """Score what matching would propose, before any person has approved or rejected anything."""
    labels = {s["id"]: s for s in load_fixture()["skills"] if s["split"] == split}
    skills = [(record, vector) for record, vector in catalog.eligible() if record.skill_id in labels]

    def no_usage(role: str, skill_id: str) -> dict[str, int]:
        return {"uses": 0, "passed": 0, "failed": 0, "idle_runs": 0}

    loadouts = compute_loadouts(
        org, skills, catalog.embedder, Decisions(work / "none.yaml"), no_usage, threshold=threshold
    )
    score = LoadoutScore(threshold)
    for ref, loadout in loadouts.items():
        wanted = {skill_id for skill_id, s in labels.items() if ref in s["roles"]}
        got = [m.record.skill_id for m in loadout.pending[: org.role(ref).max_skills]]
        correct = [skill for skill in got if skill in wanted]
        wrong = [skill for skill in got if skill not in wanted]
        missed = sorted(wanted - set(got))
        score.true_positives += len(correct)
        score.false_positives += len(wrong)
        score.false_negatives += len(missed)
        score.per_role[ref] = {"correct": correct, "wrong": wrong, "missed": missed}
        for skill in wanted:
            language = score.by_language.setdefault(labels[skill]["lang"], {"found": 0, "total": 0})
            language["total"] += 1
            language["found"] += int(skill in got)
    return score


def evaluate_tasks(org: Org, catalog: Any, threshold: float, work: Path) -> dict[str, dict[str, int]]:
    """Search only within what matching gave that role, as a real run would."""

    def no_usage(role: str, skill_id: str) -> dict[str, int]:
        return {"uses": 0, "passed": 0, "failed": 0, "idle_runs": 0}

    loadouts = compute_loadouts(
        org, catalog.eligible(), catalog.embedder, Decisions(work / "none.yaml"), no_usage, threshold=threshold
    )
    results: dict[str, dict[str, int]] = {}
    for task in load_fixture()["tasks"]:
        ids = {m.record.skill_id for m in loadouts[task["role"]].pending[: org.role(task["role"]).max_skills]}
        ranked = [h.record.skill_id for h in catalog.search(task["text"], skill_ids=ids, top_k=3)]
        row = results.setdefault(task["lang"], {"total": 0, "top_1": 0, "top_3": 0})
        row["total"] += 1
        row["top_1"] += int(ranked[:1] == [task["skill"]])
        row["top_3"] += int(task["skill"] in ranked)
    return results
