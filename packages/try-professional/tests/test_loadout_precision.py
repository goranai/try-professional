"""Loadout precision against a labelled fixture, with the real model. Runs with: pytest -m model

The threshold was chosen on other data, so these are checks against regression, and heldout is
the number to look at. The same author wrote the fixture and the roles' skill_needs, so these run
optimistic. The measurement on real skills is in docs/research/loadouts/.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from try_professional.bench.retrieval.loadout_eval import (
    LoadoutScore,
    evaluate_tasks,
    fixture_records,
    load_fixture,
    score_loadouts,
)
from try_professional.memory import FastEmbedEmbedder
from try_professional.org import Org
from try_professional.skills import MATCH_THRESHOLD, SkillCatalog

pytestmark = pytest.mark.model


@pytest.fixture(scope="module")
def catalog(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SkillCatalog]:
    built = SkillCatalog(tmp_path_factory.mktemp("catalog") / "c.sqlite", FastEmbedEmbedder())
    built.rebuild(fixture_records())
    yield built
    built.close()


@pytest.fixture
def heldout(org: Org, catalog: SkillCatalog, tmp_path: Path) -> LoadoutScore:
    return score_loadouts(org, catalog, "heldout", MATCH_THRESHOLD, tmp_path)


def _all_splits(org: Org, catalog: SkillCatalog, role: str, tmp_path: Path) -> set[str]:
    found: set[str] = set()
    for split in ("dev", "heldout"):
        result = score_loadouts(org, catalog, split, MATCH_THRESHOLD, tmp_path).per_role[role]
        found |= {*result["correct"], *result["wrong"]}
    return found


def test_heldout_precision_and_recall(heldout: LoadoutScore) -> None:
    assert heldout.precision >= 0.90, heldout.per_role
    assert heldout.recall >= 0.70, heldout.per_role


def test_indonesian_skills_are_matched_from_english_needs(heldout: LoadoutScore) -> None:
    indonesian = heldout.by_language["id"]
    assert indonesian["total"] >= 10
    assert indonesian["found"] / indonesian["total"] >= 0.70


def test_backend_gets_backend_skills_and_no_marketing_ones(org: Org, catalog: SkillCatalog, tmp_path: Path) -> None:
    found = _all_splits(org, catalog, "engineering/backend", tmp_path)
    assert {"rest-api-design", "sql-migrations"} <= found
    assert not found & {"blog-writer", "keyword-research", "campaign-planner", "social-calendar"}


def test_content_writer_gets_writing_skills_and_no_engineering_ones(
    org: Org, catalog: SkillCatalog, tmp_path: Path
) -> None:
    found = _all_splits(org, catalog, "marketing/content-writer", tmp_path)
    assert {"blog-writer", "copy-editor"} <= found
    assert not found & {"rest-api-design", "k8s-deploy", "sql-migrations"}


def test_counsel_gets_legal_skills(org: Org, catalog: SkillCatalog, tmp_path: Path) -> None:
    found = _all_splits(org, catalog, "legal/counsel", tmp_path)
    assert {"contract-review", "license-compat"} <= found
    assert not found & {"react-components", "blog-writer"}


def test_every_role_gets_something_to_work_with(org: Org, catalog: SkillCatalog, tmp_path: Path) -> None:
    for role in org.roles:
        assert _all_splits(org, catalog, role, tmp_path), role


def test_near_duplicates_in_the_fixture_are_not_collapsed_by_accident(org: Org, catalog: SkillCatalog) -> None:
    """Different skills must stay separate, or collapsing would quietly hide real options."""
    records = {r.skill_id: v for r, v in catalog.eligible()}
    assert float(records["blog-writer"] @ records["copy-editor"]) < 0.92
    assert float(records["k8s-deploy"] @ records["github-actions"]) < 0.92


def test_task_queries_in_english_and_indonesian_find_the_skill(org: Org, catalog: SkillCatalog, tmp_path: Path) -> None:
    results = evaluate_tasks(org, catalog, MATCH_THRESHOLD, tmp_path)
    for language in ("en", "id"):
        assert results[language]["total"] >= 5
        assert results[language]["top_3"] / results[language]["total"] >= 0.70, results
    assert {t["lang"] for t in load_fixture()["tasks"]} >= {"en", "id"}
