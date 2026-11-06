"""Memory access rules, and the checks that keep text on this machine. No model needed."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from try_professional.adapters.api_runner.tools import ToolContext, ToolRefused, run_tool
from try_professional.core.ports import NullSkills
from try_professional.memory import (
    COMPANY_BANK,
    MemoryAccessDenied,
    MemoryConfigError,
    MemoryGateway,
    MemoryHit,
    MemorySettings,
    RemoteEmbeddingRefused,
    check_local_only,
    department_bank,
    personal_bank,
    remote_embedding_reasons,
    score_floor,
)
from try_professional.memory.embedding import DEFAULT_MODEL, normalize
from try_professional.memory.settings import apply_to_environment, check_model_unchanged
from try_professional.org import Org
from try_professional.skills import CatalogModelMismatch, CatalogSkills, SkillCatalog, SkillRecord


class FakeEmbedder:
    """Deterministic bag-of-words vectors, so catalog logic can be tested with no model."""

    def __init__(self, model_name: str = "fake-model") -> None:
        self.model_name = model_name

    def embed_query(self, text: str) -> Any:
        return self.embed_documents([text])[0]

    def embed_documents(self, texts: list[str]) -> Any:
        rows = []
        for text in texts:
            row = np.zeros(64, dtype=np.float32)
            for word in text.lower().replace(":", " ").split():
                row[int(hashlib.sha256(word.encode()).hexdigest(), 16) % 64] += 1.0
            rows.append(row)
        return normalize(np.array(rows))


class FakeBackend:
    def __init__(self) -> None:
        self.banks: dict[str, list[str]] = {}
        self.sources: list[str] = []

    def remember(self, bank: str, text: str, *, source: str) -> None:
        self.banks.setdefault(bank, []).append(text)
        self.sources.append(source)

    def recall(self, bank: str, query: str, top_k: int) -> list[MemoryHit]:
        words = set(query.lower().split())
        hits = []
        for text in self.banks.get(bank, []):
            overlap = len(words & set(text.lower().split()))
            hits.append(MemoryHit(text, overlap / max(len(words), 1), bank))
        return hits[:top_k]


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def gateway(org: Org, backend: FakeBackend) -> MemoryGateway:
    return MemoryGateway(org, backend, floor=0.1)


class TestAccessRules:
    def test_a_role_writes_only_to_its_own_personal_memory(self, gateway: MemoryGateway, backend: FakeBackend) -> None:
        gateway.write("engineering/backend", "deploy on friday")
        assert backend.banks == {personal_bank("engineering/backend"): ["deploy on friday"]}

    def test_personal_memory_is_private(self, gateway: MemoryGateway) -> None:
        gateway.write("engineering/backend", "deploy on friday")
        assert any("friday" in h for h in gateway.recall("engineering/backend", "deploy friday"))
        assert gateway.recall("engineering/qa", "deploy friday") == []
        assert gateway.recall("engineering/tech-lead", "deploy friday") == [], "not even its manager"
        assert gateway.recall_personal("engineering/qa", "deploy friday") == []

    def test_only_the_head_writes_department_memory(self, gateway: MemoryGateway, backend: FakeBackend) -> None:
        with pytest.raises(MemoryAccessDenied, match="head"):
            gateway.write_department("engineering/backend", "deploy on friday")
        assert backend.banks == {}
        gateway.write_department("engineering/tech-lead", "deploy on friday")
        assert backend.banks == {department_bank("engineering"): ["deploy on friday"]}

    def test_the_department_shares_memory(self, gateway: MemoryGateway) -> None:
        gateway.write_department("engineering/tech-lead", "deploy on friday")
        assert any("friday" in h for h in gateway.recall("engineering/qa", "deploy friday"))

    def test_another_department_cannot_read_it(self, gateway: MemoryGateway) -> None:
        gateway.write_department("engineering/tech-lead", "deploy on friday")
        assert gateway.recall("marketing/social", "deploy friday") == []

    def test_a_prompt_never_gets_a_whole_bank(self, gateway: MemoryGateway, backend: FakeBackend) -> None:
        for n in range(30):
            gateway.write_department("engineering/tech-lead", f"deploy note {n} " + "x" * 900)
            gateway.write("engineering/backend", f"deploy note {n}")
        found = gateway.recall("engineering/backend", "deploy note")
        assert len(found) <= 7
        assert all(len(text) < 320 for text in found)

    def test_everyone_reads_the_company_bank(self, org: Org, gateway: MemoryGateway) -> None:
        gateway.promote("executive/ceo", "launch is in march", "art-1")
        for role in org.roles:
            assert any("march" in h for h in gateway.recall(role, "launch march")), role

    def test_a_role_reads_exactly_three_banks(self, gateway: MemoryGateway) -> None:
        assert gateway.readable_banks("sales/sales-lead") == [
            personal_bank("sales/sales-lead"),
            "dept-sales",
            COMPANY_BANK,
        ]

    @pytest.mark.parametrize("role", ["engineering/backend", "engineering/tech-lead", "legal/counsel"])
    def test_only_executive_can_promote(self, gateway: MemoryGateway, backend: FakeBackend, role: str) -> None:
        with pytest.raises(MemoryAccessDenied, match="company memory"):
            gateway.promote(role, "text", "art-1")
        assert COMPANY_BANK not in backend.banks

    def test_chief_of_staff_can_promote(self, gateway: MemoryGateway, backend: FakeBackend) -> None:
        gateway.promote("executive/chief-of-staff", "text", "art-1")
        assert backend.banks[COMPANY_BANK] == ["text"]

    def test_promotion_needs_a_source(self, gateway: MemoryGateway) -> None:
        with pytest.raises(MemoryAccessDenied, match="source"):
            gateway.promote("executive/ceo", "text", "  ")

    def test_promotion_records_who_and_from_what(self, gateway: MemoryGateway, backend: FakeBackend) -> None:
        gateway.promote("executive/ceo", "text", "art-1")
        assert backend.sources == ["executive/ceo from art-1"]

    @pytest.mark.parametrize("role", ["ghost/role", "engineering/ghost", "", "human"])
    def test_unknown_roles_get_nothing(self, gateway: MemoryGateway, role: str) -> None:
        with pytest.raises(MemoryAccessDenied):
            gateway.recall(role, "q")
        with pytest.raises(MemoryAccessDenied):
            gateway.write(role, "text")

    def test_empty_and_oversized_text_are_refused(self, gateway: MemoryGateway) -> None:
        with pytest.raises(MemoryAccessDenied, match="empty"):
            gateway.write("engineering/qa", "   ")
        with pytest.raises(MemoryAccessDenied, match="over"):
            gateway.write("engineering/qa", "x" * 5000)

    def test_a_model_cannot_name_another_bank_through_the_tool(
        self, gateway: MemoryGateway, backend: FakeBackend, tmp_path: Path
    ) -> None:
        context = ToolContext("engineering/backend", ("memory_write",), tmp_path, gateway, NullSkills())
        run_tool(context, "memory_write", {"text": "hello", "bank": COMPANY_BANK, "role": "executive/ceo"})
        assert set(backend.banks) == {personal_bank("engineering/backend")}

    def test_refused_writes_become_a_tool_refusal(self, gateway: MemoryGateway, tmp_path: Path) -> None:
        context = ToolContext("engineering/backend", ("memory_write",), tmp_path, gateway, NullSkills())
        with pytest.raises(ToolRefused, match="empty"):
            run_tool(context, "memory_write", {"text": ""})


class TestFloor:
    def test_results_below_the_floor_are_dropped(self, org: Org, backend: FakeBackend) -> None:
        backend.remember("dept-sales", "alpha beta", source="s")
        gateway = MemoryGateway(org, backend, floor=0.9)
        assert gateway.recall("sales/sales-lead", "alpha unrelated") == []

    def test_results_are_ordered_and_limited_across_banks(self, org: Org, backend: FakeBackend) -> None:
        for n in range(4):
            backend.remember("dept-sales", f"alpha {n}", source="s")
            backend.remember(COMPANY_BANK, f"alpha beta {n}", source="s")
        gateway = MemoryGateway(org, backend, floor=0.0, top_k=3)
        found = gateway.search("sales/sales-lead", "alpha beta")
        assert len(found) == 3
        assert [h.bank for h in found[:2]] == [COMPANY_BANK, COMPANY_BANK], "best first, within each tier's limit"

    def test_an_unknown_model_gets_no_floor(self) -> None:
        assert score_floor(DEFAULT_MODEL) > 0
        assert score_floor("some/other-model") == 0.0


class TestKeepingTextLocal:
    def _settings(self, tmp_path: Path, **kwargs: Any) -> MemorySettings:
        return MemorySettings(data_dir=tmp_path, **kwargs)

    def test_the_default_setup_is_local(self, tmp_path: Path) -> None:
        check_local_only(self._settings(tmp_path), {})

    def test_openai_api_key_alone_does_not_make_it_remote(self, tmp_path: Path) -> None:
        check_local_only(self._settings(tmp_path), {"OPENAI_API_KEY": "x", "TRYPRO_LLM_API_KEY": "y"})

    @pytest.mark.parametrize(
        "env",
        [
            {"MNEMOSYNE_EMBEDDING_API_URL": "https://embeddings.example.com/v1"},
            {"MNEMOSYNE_EMBEDDINGS_VIA_API": "true"},
            {"MNEMOSYNE_EMBEDDINGS_VIA_API": "1"},
        ],
    )
    def test_settings_that_send_text_away_are_refused(self, tmp_path: Path, env: dict[str, str]) -> None:
        with pytest.raises(RemoteEmbeddingRefused, match="leave this machine"):
            check_local_only(self._settings(tmp_path), env)

    @pytest.mark.parametrize("model", ["openai/text-embedding-3-small", "text-embedding-ada-002"])
    def test_api_shaped_model_names_are_refused(self, model: str) -> None:
        assert remote_embedding_reasons(model, {})

    def test_remote_needs_an_explicit_choice(self, tmp_path: Path) -> None:
        settings = self._settings(tmp_path, remote_allowed=True)
        check_local_only(settings, {"MNEMOSYNE_EMBEDDING_API_URL": "https://x"})

    def test_settings_are_applied(self, tmp_path: Path) -> None:
        env: dict[str, str] = {}
        apply_to_environment(self._settings(tmp_path), env)
        assert env["MNEMOSYNE_LEXICAL_GATE_MIN"] == "0"
        assert env["MNEMOSYNE_EMBEDDING_MODEL"] == DEFAULT_MODEL
        assert env["MNEMOSYNE_EMBEDDING_DIM"] == "384"
        assert env["MNEMOSYNE_DATA_DIR"] == str(tmp_path)
        assert int(env["MNEMOSYNE_WM_TTL_HOURS"]) > 24 * 365 * 10

    def test_refusal_happens_before_anything_is_applied(self, tmp_path: Path) -> None:
        env = {"MNEMOSYNE_EMBEDDINGS_VIA_API": "on"}
        with pytest.raises(RemoteEmbeddingRefused):
            apply_to_environment(self._settings(tmp_path), env)
        assert "MNEMOSYNE_LEXICAL_GATE_MIN" not in env

    def test_unknown_model_fails_clearly(self, tmp_path: Path) -> None:
        with pytest.raises(MemoryConfigError, match="unknown embedding model"):
            apply_to_environment(self._settings(tmp_path, embedding_model="some/model"), {})

    def test_changing_the_model_on_existing_memory_is_refused(self, tmp_path: Path) -> None:
        check_model_unchanged(self._settings(tmp_path))
        check_model_unchanged(self._settings(tmp_path))
        (tmp_path / "trypro-memory.json").write_text(json.dumps({"embedding_model": "old/model"}), encoding="utf-8")
        with pytest.raises(MemoryConfigError, match="old/model"):
            check_model_unchanged(self._settings(tmp_path))


def _record(skill_id: str, description: str, **kwargs: Any) -> SkillRecord:
    return SkillRecord(skill_id=skill_id, name=skill_id, description=description, category="general", **kwargs)


class TestCatalog:
    @pytest.fixture
    def catalog(self, tmp_path: Path) -> SkillCatalog:
        return SkillCatalog(tmp_path / "c.sqlite", FakeEmbedder())

    def test_every_field_round_trips(self, catalog: SkillCatalog) -> None:
        record = SkillRecord(
            skill_id="owner/repo/seo",
            name="seo",
            description="audit search ranking",
            category="seo",
            departments=("marketing",),
            source_repo="owner/repo",
            commit_sha="a" * 40,
            license="MIT",
            trust="verified",
        )
        catalog.rebuild([record])
        assert catalog.search("audit search ranking")[0].record == record

    def test_quarantined_skills_are_never_returned(self, catalog: SkillCatalog) -> None:
        catalog.rebuild([_record("bad", "deploy service", trust="quarantined"), _record("ok", "deploy service")])
        assert [h.record.skill_id for h in catalog.search("deploy service")] == ["ok"]

    def test_unknown_trust_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="trust"):
            _record("x", "y", trust="probably-fine")

    def test_categories_filter_before_ranking(self, catalog: SkillCatalog) -> None:
        catalog.rebuild(
            [
                SkillRecord("a", "a", "deploy service", "devops"),
                SkillRecord("b", "b", "deploy service", "marketing"),
            ]
        )
        assert [h.record.skill_id for h in catalog.search("deploy", categories=("marketing",))] == ["b"]
        assert catalog.search("deploy", categories=()) == []

    def test_ranking_is_repeatable_and_does_not_change_the_catalog(self, catalog: SkillCatalog, tmp_path: Path) -> None:
        catalog.rebuild([_record(f"s{n}", "same text") for n in range(6)])
        first = [h.record.skill_id for h in catalog.search("same text")]
        before = (tmp_path / "c.sqlite").read_bytes()
        for _ in range(3):
            assert [h.record.skill_id for h in catalog.search("same text")] == first
        assert (tmp_path / "c.sqlite").read_bytes() == before, "reading must not change anything"
        assert first == ["s0", "s1", "s2", "s3", "s4"]

    def test_there_is_no_floor_and_each_result_carries_its_score(self, catalog: SkillCatalog) -> None:
        catalog.rebuild([_record("a", "deploy service")])
        hits = catalog.search("unrelated words entirely")
        assert [h.record.skill_id for h in hits] == ["a"]
        assert hits[0].score < 0.5

    def test_rebuild_replaces_everything(self, catalog: SkillCatalog) -> None:
        catalog.rebuild([_record("a", "one"), _record("b", "two")])
        catalog.rebuild([_record("c", "three")])
        assert catalog.count() == 1

    def test_failed_rebuild_keeps_the_old_catalog(self, catalog: SkillCatalog) -> None:
        catalog.rebuild([_record("a", "one")])
        with pytest.raises(Exception, match=r"(?i)unique"):
            catalog.rebuild([_record("b", "two"), _record("b", "again")])
        assert catalog.count() == 1
        assert catalog.search("one")[0].record.skill_id == "a"

    def test_empty_catalog_returns_nothing(self, catalog: SkillCatalog) -> None:
        assert catalog.search("anything") == []

    def test_a_different_model_cannot_read_old_vectors(self, tmp_path: Path) -> None:
        path = tmp_path / "c.sqlite"
        first = SkillCatalog(path, FakeEmbedder("model-a"))
        first.rebuild([_record("a", "one")])
        first.close()
        with pytest.raises(CatalogModelMismatch, match="model-a"):
            SkillCatalog(path, FakeEmbedder("model-b"))

    def test_roles_only_see_their_allowed_categories(self, org: Org, catalog: SkillCatalog) -> None:
        catalog.rebuild(
            [
                SkillRecord("be", "be", "write tests", "testing"),
                SkillRecord("mk", "mk", "write tests", "seo"),
            ]
        )
        skills = CatalogSkills(org, catalog)
        assert [h["skill_id"] for h in skills.search("engineering/backend", "write tests")] == ["be"]
        assert [h["skill_id"] for h in skills.search("marketing/seo", "write tests")] == ["mk"]

    def test_results_fit_a_skill_resolution_artifact(self, org: Org, catalog: SkillCatalog) -> None:
        from try_professional.core.artifacts import make_artifact

        catalog.rebuild([SkillRecord("be", "be", "write tests", "testing")])
        candidates = CatalogSkills(org, catalog).search("engineering/backend", "write tests")
        make_artifact(
            "SkillResolution", {"query": "q", "candidates": candidates}, run_id="r", produced_by="orchestrator"
        )


class TestPipelineUsesMemory:
    def test_tasks_receive_recalled_context(self, org: Org, harness: Callable[..., Any], backend: FakeBackend) -> None:
        gateway = MemoryGateway(org, backend, floor=0.0)
        gateway.write("engineering/backend", "goal hint from last time")
        run = harness(org, memory=gateway)
        result = run.runner.run("goal")
        state = run.orchestrator.state(result.run_id)
        task = next(t for t in state.tasks.values() if t.work_package_id)
        assert any("hint" in m for m in task.memory_context)

    def test_lessons_become_proposals_and_never_reach_company_memory_alone(
        self, org: Org, harness: Callable[..., Any]
    ) -> None:
        run = harness(org)
        result = run.runner.run("Goal")
        state = run.orchestrator.state(result.run_id)
        assert run.memory.promoted == []
        pending = run.orchestrator.company.pending_memory("company")
        assert [p.proposed_by for p in pending] == [org.root_role]
        assert pending[0].source == state.retrospective_artifact_id
        assert any(r["kind"] == "lessons_proposed" for r in run.records(result.run_id))

    def test_a_memory_failure_does_not_stop_the_run(self, org: Org, harness: Callable[..., Any]) -> None:
        class Broken:
            def recall(self, role: str, query: str) -> list[str]:
                raise RuntimeError("disk full")

            def write(self, role: str, text: str) -> None:
                raise RuntimeError("disk full")

            def promote(self, role: str, text: str, source: str) -> None:
                raise RuntimeError("disk full")

        run = harness(org, memory=Broken())
        result = run.runner.run("Goal")
        assert result.status == "done"
        errors = [r for r in run.records(result.run_id) if r["kind"] == "memory_error"]
        assert errors and "disk full" not in str(errors), "details stay out of the log"


class TestE5Option:
    """e5-large is a documented upgrade. Its prefixes have to reach both paths that embed text."""

    E5 = "intfloat/multilingual-e5-large"

    def test_prefixes_reach_the_mnemosyne_settings(self, tmp_path: Path) -> None:
        env: dict[str, str] = {}
        apply_to_environment(MemorySettings(data_dir=tmp_path, embedding_model=self.E5), env)
        assert env["MNEMOSYNE_EMBEDDING_QUERY_PREFIX"] == "query: "
        assert env["MNEMOSYNE_EMBEDDING_DOC_PREFIX"] == "passage: "
        assert env["MNEMOSYNE_EMBEDDING_DIM"] == "1024"

    def test_the_default_model_clears_any_prefix(self, tmp_path: Path) -> None:
        env = {"MNEMOSYNE_EMBEDDING_QUERY_PREFIX": "query: "}
        apply_to_environment(MemorySettings(data_dir=tmp_path), env)
        assert env["MNEMOSYNE_EMBEDDING_QUERY_PREFIX"] == ""

    def test_catalog_embedder_applies_the_right_prefix_to_each_side(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from try_professional.memory import FastEmbedEmbedder

        seen: list[str] = []

        class Stub:
            def embed(self, texts: list[str]) -> Any:
                seen.extend(texts)
                return iter([np.ones(4, dtype=np.float32) for _ in texts])

        embedder = FastEmbedEmbedder(self.E5)
        monkeypatch.setattr(embedder, "_load", Stub)
        embedder.embed_documents(["a doc"])
        embedder.embed_query("a question")
        assert seen == ["passage: a doc", "query: a question"]

        seen.clear()
        plain = FastEmbedEmbedder()
        monkeypatch.setattr(plain, "_load", Stub)
        plain.embed_documents(["a doc"])
        plain.embed_query("a question")
        assert seen == ["a doc", "a question"]

    def test_e5_has_no_memory_floor_because_none_was_measured_to_work(self) -> None:
        assert score_floor(self.E5) == 0.0
