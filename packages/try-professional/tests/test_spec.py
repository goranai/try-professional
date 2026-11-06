"""The spec's own guarantees: conformance fixtures, no drift, and parallel query files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from try_professional.core.artifacts import artifact_schemas
from try_professional.org import load_bundle, validate_bundle

ROOT = Path(__file__).resolve().parents[3]
SPEC = ROOT / "spec"
PACKAGE = ROOT / "packages" / "try-professional" / "src" / "try_professional"
RETRIEVAL = PACKAGE / "bench" / "retrieval"


def _cases(group: str) -> list[Path]:
    return sorted((SPEC / "conformance" / group).glob("*.yaml"))


def _load(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded


@pytest.mark.parametrize("path", _cases("valid"), ids=lambda p: p.stem)
def test_valid_fixtures(path: Path) -> None:
    case = _load(path)
    issues = validate_bundle(case["org"], case["roles"])
    assert [i for i in issues if i.severity == "error"] == []
    for code in case["expect"].get("warnings", []):
        assert code in {i.code for i in issues if i.severity == "warning"}
    load_bundle(case["org"], case["roles"])


@pytest.mark.parametrize("path", _cases("invalid"), ids=lambda p: p.stem)
def test_invalid_fixtures(path: Path) -> None:
    case = _load(path)
    issues = validate_bundle(case["org"], case["roles"])
    codes = {i.code for i in issues if i.severity == "error"}
    assert codes, "expected this fixture to be rejected"
    for code in case["expect"]["errors"]:
        assert code in codes, f"{path.stem}: wanted {code}, got {codes}"


def test_every_semantic_rule_has_a_fixture() -> None:
    text = (ROOT / "docs" / "spec" / "org-v1.md").read_text(encoding="utf-8")
    rules = {word for word in text.replace("`", " ").split() if word.startswith("ORG0") and len(word) == 6}
    covered = {code for path in _cases("invalid") for code in _load(path)["expect"]["errors"]}
    assert rules - covered == set()


@pytest.mark.parametrize("name", ["org-v1.schema.json", "role-v1.schema.json"])
def test_packaged_schema_matches_canonical(name: str) -> None:
    assert (PACKAGE / "_spec" / name).read_bytes() == (SPEC / name).read_bytes()


def test_artifact_schemas_have_not_drifted() -> None:
    generated = artifact_schemas()
    directory = SPEC / "artifacts-v1"
    assert {p.name for p in directory.glob("*.json")} == set(generated)
    for name, schema in generated.items():
        assert json.loads((directory / name).read_text(encoding="utf-8")) == schema, (
            f"{name} drifted: run scripts/generate_schemas.py"
        )


class TestQueryFiles:
    LANGUAGES = ("en", "id", "es", "zh", "ar")

    def _queries(self, language: str) -> dict[str, Any]:
        loaded: dict[str, Any] = yaml.safe_load(
            (RETRIEVAL / "queries" / f"{language}.yaml").read_text(encoding="utf-8")
        )
        return loaded

    def test_all_languages_ask_for_the_same_skills(self) -> None:
        sets = {
            lang: sorted((q["skill"], q["split"]) for q in self._queries(lang)["queries"]) for lang in self.LANGUAGES
        }
        for lang in self.LANGUAGES:
            assert sets[lang] == sets["en"], lang

    def test_every_query_targets_a_catalog_skill(self) -> None:
        skills = {s["id"] for s in yaml.safe_load((RETRIEVAL / "skills.yaml").read_text(encoding="utf-8"))["skills"]}
        assert len(skills) == 40
        for lang in self.LANGUAGES:
            for query in self._queries(lang)["queries"]:
                assert query["skill"] in skills

    def test_each_skill_has_a_dev_and_a_heldout_query(self) -> None:
        queries = self._queries("en")["queries"]
        for skill in {q["skill"] for q in queries}:
            assert sorted(q["split"] for q in queries if q["skill"] == skill) == ["dev", "heldout"]

    def test_review_status_is_declared(self) -> None:
        for lang in self.LANGUAGES:
            review = self._queries(lang)["review"]
            assert review["status"] in {"pending", "unreviewed", "reviewed", "not-required"}
            if review["status"] == "reviewed":
                assert review["reviewer"], lang

    def test_no_query_repeats_a_catalog_description(self) -> None:
        skills = yaml.safe_load((RETRIEVAL / "skills.yaml").read_text(encoding="utf-8"))["skills"]
        descriptions = {s["description"].lower() for s in skills}
        for query in self._queries("en")["queries"]:
            assert query["text"].lower() not in descriptions
