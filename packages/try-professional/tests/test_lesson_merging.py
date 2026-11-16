"""Repeated lessons keep their evidence without taking another place in a brief."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from try_professional.adapters.api_runner.runner import ApiRunner, ModelRoutes
from try_professional.adapters.api_runner.scripted import ScriptedClient, default_payload
from try_professional.adapters.mcp import McpService
from try_professional.core.company import _MIGRATIONS, CompanyStore
from try_professional.core.orchestrator import Orchestrator
from try_professional.core.pipeline import Purpose
from try_professional.memory import DEFAULT_MODEL, MemoryGateway, MemoryHit
from try_professional.memory.embedding import FastEmbedEmbedder, Vectors
from try_professional.memory.lessons import LESSON_SIMILARITY, closest_lesson
from try_professional.org import Org

DATA = Path(__file__).with_name("data") / "lesson_similarity.json"
MEASURED = json.loads(DATA.read_text(encoding="utf-8"))
FIRST = MEASURED["pairs"][0]["first"]
PARAPHRASE = MEASURED["pairs"][0]["second"]
DISTINCT = MEASURED["pairs"][1]["second"]


class MeasuredEmbedder:
    """Replay document vectors measured with the existing local model on 2026-11-16."""

    model_name = DEFAULT_MODEL

    def embed_documents(self, texts: list[str]) -> Vectors:
        return np.asarray([MEASURED["vectors"][text] for text in texts], dtype=np.float32)

    def embed_query(self, text: str) -> Vectors:
        return self.embed_documents([text])[0]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CompanyStore]:
    memory = CompanyStore(tmp_path / "company.sqlite", embedder=MeasuredEmbedder())
    yield memory
    memory.close()


def learn(store: CompanyStore, text: str, run: str, tier: str = "company", owner: str = "company") -> Any:
    return store.record_memory(
        tier, owner, text, source_run=run, source="artifact-1", proposed_by="engineering/qa", at="2026-11-16"
    )


@pytest.mark.parametrize("tier,owner", [("company", "company"), ("department", "engineering")])
def test_a_paraphrase_merges_and_keeps_each_runs_evidence_and_learning_count(
    store: CompanyStore, tier: str, owner: str
) -> None:
    first, added, learned = learn(store, FIRST, "run-1", tier, owner)
    assert added and learned
    second, added, learned = learn(store, PARAPHRASE, "run-2", tier, owner)
    assert second.id == first.id and not added and learned
    third, added, learned = learn(store, FIRST, "run-3", tier, owner)
    assert third.id == first.id and third.text == FIRST and third.learned_count == 3
    assert [(e.source_run, e.source, e.text) for e in third.evidence] == [
        ("run-1", "artifact-1", FIRST),
        ("run-2", "artifact-1", PARAPHRASE),
        ("run-3", "artifact-1", FIRST),
    ]
    assert len(store.pending_memory(tier, owner)) == 1


def test_replaying_one_learning_event_does_not_increase_its_count(store: CompanyStore) -> None:
    first, _, _ = learn(store, FIRST, "run-1")
    repeated, added, learned = learn(store, FIRST, "run-1")
    assert repeated.id == first.id and not added and not learned and repeated.learned_count == 1


@pytest.mark.parametrize("accepted", [True, False])
def test_a_later_paraphrase_keeps_the_original_approval_or_rejection(store: CompanyStore, accepted: bool) -> None:
    first, _, _ = learn(store, FIRST, "run-1")
    store.decide_memory(first.id, accepted, by="person", at="2026-11-16")
    second, added, _ = learn(store, PARAPHRASE, "run-2")
    assert not added and second.status == ("accepted" if accepted else "rejected")
    assert second.learned_count == 2 and store.pending_memory("company") == []


def test_distinct_lessons_and_other_teams_keep_their_own_entries(store: CompanyStore) -> None:
    first, _, _ = learn(store, FIRST, "run-1", "department", "engineering")
    distinct, added, _ = learn(store, DISTINCT, "run-2", "department", "engineering")
    other_team, other_added, _ = learn(store, PARAPHRASE, "run-3", "department", "research")
    assert added and other_added and len({first.id, distinct.id, other_team.id}) == 3


def test_evidence_and_count_survive_reopening_the_company(store: CompanyStore) -> None:
    first, _, _ = learn(store, FIRST, "run-1")
    learn(store, PARAPHRASE, "run-2")
    reopened = CompanyStore(store.path, embedder=MeasuredEmbedder())
    try:
        lesson = reopened.pending_memory("company")[0]
        assert lesson.id == first.id and lesson.learned_count == 2
        assert [e.text for e in lesson.evidence] == [FIRST, PARAPHRASE]
    finally:
        reopened.close()


def test_a_legacy_proposal_keeps_its_original_evidence_when_upgraded(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "company.sqlite"
    with sqlite3.connect(path) as connection:
        for number, migration in enumerate(_MIGRATIONS[:5], 1):
            connection.executescript(f"{migration} PRAGMA user_version = {number};")
        connection.execute(
            "INSERT INTO memory_proposals (tier,owner,text,source_run,source,proposed_by,status,created_at) VALUES ('company','company',?,'old-run','old-source','old-role','pending','old-date')",
            (FIRST,),
        )
    store = CompanyStore(path, embedder=MeasuredEmbedder())
    try:
        lesson, added, _ = learn(store, PARAPHRASE, "new-run")
        assert not added and lesson.learned_count == 2
        assert lesson.evidence[0].reference == "old-run: old-source, learned by old-role at old-date"
    finally:
        store.close()


@pytest.mark.parametrize("pair", MEASURED["pairs"], ids=lambda p: f"{p['kind']}: {p['second']}")
def test_the_measured_threshold_merges_close_paraphrases_and_keeps_distinct_lessons(pair: dict[str, Any]) -> None:
    vectors = MeasuredEmbedder().embed_documents([pair["first"], pair["second"]])
    assert float(vectors[0] @ vectors[1]) == pytest.approx(pair["score"], abs=1e-6)
    assert (closest_lesson(vectors[1], [vectors[0]], DEFAULT_MODEL) == 0) == (pair["kind"] == "paraphrase")
    assert LESSON_SIMILARITY[DEFAULT_MODEL] == MEASURED["threshold"] == 0.95


def test_an_unmeasured_model_does_not_inherit_another_models_threshold() -> None:
    vector = MeasuredEmbedder().embed_documents([FIRST])[0]
    assert closest_lesson(vector, [vector], "intfloat/multilingual-e5-large") is None


class ProposalBackend:
    embedder = MeasuredEmbedder()

    def __init__(self) -> None:
        self.writes: list[tuple[str, str, str]] = []

    def remember(self, bank: str, text: str, *, source: str) -> None:
        self.writes.append((bank, text, source))

    def recall(self, bank: str, query: str, top_k: int) -> list[MemoryHit]:
        return []

    def size(self, bank: str) -> dict[str, int]:
        return {"entries": 0, "bytes": 0}


class RecallBackend(ProposalBackend):
    def __init__(self, hits: dict[str, list[MemoryHit]]) -> None:
        super().__init__()
        self.hits = hits
        self.reads: list[str] = []

    def recall(self, bank: str, query: str, top_k: int) -> list[MemoryHit]:
        self.reads.append(bank)
        return self.hits.get(bank, [])[:top_k]


def delivered_assignment(company: Orchestrator) -> str:
    service = McpService(company)
    current = service.run_start("Review cited files")
    run_id = current["run_id"]
    for _ in range(4):
        action = current["actions"][0]
        if action["purpose"] == "execute":
            bound = McpService(company, bound_role=action["role"])
            return str(bound.run_next_action(run_id)["actions"][0]["assignment"])
        state = company.state(run_id)
        invocation = state.invocations[action["invocation_id"]]
        current = service.artifact_submit(
            run_id, action["invocation_id"], default_payload(invocation, state, company.org)
        )
        assert current["accepted"], current
    raise AssertionError("the task was never delivered")


@pytest.mark.parametrize("company_text", [FIRST, PARAPHRASE])
def test_the_delivered_assignment_shows_equivalent_readable_lessons_once_with_their_tiers(
    org: Org, tmp_path: Path, company_text: str
) -> None:
    hits = {
        "agent-engineering--backend": [MemoryHit(FIRST, 1.0, "agent-engineering--backend")],
        "dept-engineering": [MemoryHit(PARAPHRASE, 0.9, "dept-engineering")],
        "company": [MemoryHit(company_text, 0.8, "company"), MemoryHit(DISTINCT, 0.7, "company")],
        "agent-engineering--qa": [MemoryHit(DISTINCT, 1.0, "agent-engineering--qa")],
        "dept-marketing": [MemoryHit(DISTINCT, 1.0, "dept-marketing")],
    }
    backend = RecallBackend(hits)
    company = Orchestrator(org, tmp_path / "home", memory=MemoryGateway(org, backend))
    try:
        assignment = delivered_assignment(company)
        assert "## Relevant memory" in assignment
        assert assignment.count(FIRST) == 1 and PARAPHRASE not in assignment
        assert "[personal, dept-engineering, company] " + FIRST in assignment
        assert assignment.count(DISTINCT) == 1
        assert "agent-engineering--qa" not in backend.reads and "dept-marketing" not in backend.reads
    finally:
        company.close()


def test_recall_groups_lessons_before_limiting_and_keeps_only_readable_provenance(org: Org) -> None:
    hits = {
        "agent-engineering--backend": [MemoryHit(FIRST, 1.0, "agent-engineering--backend")],
        "dept-engineering": [MemoryHit(PARAPHRASE, 0.9, "dept-engineering")],
        "company": [MemoryHit(FIRST, 0.8, "company"), MemoryHit(DISTINCT, 0.7, "company")],
    }
    backend = RecallBackend(hits)
    gateway = MemoryGateway(org, backend, top_k=2)
    assert gateway.recall("engineering/backend", FIRST) == [
        "[personal, dept-engineering, company] " + FIRST,
        "[company] " + DISTINCT,
    ]
    assert [hit.text for hit in gateway.search("engineering/backend", FIRST)] == [FIRST, DISTINCT]
    assert gateway.recall("marketing/social", FIRST) == ["[company] " + FIRST, "[company] " + DISTINCT]
    assert set(backend.reads) == {
        "agent-engineering--backend",
        "dept-engineering",
        "company",
        "agent-marketing--social",
        "dept-marketing",
    }


@pytest.mark.parametrize("with_embedder", [True, False])
def test_recall_without_a_measured_model_groups_only_identical_lessons(org: Org, with_embedder: bool) -> None:
    from .test_memory import FakeBackend, FakeEmbedder

    backend: Any = FakeBackend()
    if with_embedder:
        backend.embedder = FakeEmbedder()
    backend.remember("agent-engineering--backend", FIRST, source="run-1")
    backend.remember("dept-engineering", FIRST, source="run-2")
    backend.remember("company", PARAPHRASE, source="run-3")
    assert MemoryGateway(org, backend).recall("engineering/backend", "") == [
        "[personal, dept-engineering] " + FIRST,
        "[company] " + PARAPHRASE,
    ]


def test_team_briefs_show_a_merged_lesson_once_and_adoption_carries_all_evidence(org: Org, tmp_path: Path) -> None:
    backend = ProposalBackend()
    company = Orchestrator(org, tmp_path / "home", memory=MemoryGateway(org, backend))
    routes = ModelRoutes({"standard": "m", "executive": "m", "fast": "m"})
    try:
        for text in (FIRST, PARAPHRASE):

            def notes(invocation: Any, state: Any, org_: Org, text: str = text) -> Any:
                if invocation.purpose == Purpose.EXECUTE:
                    return {"payload": default_payload(invocation, state, org_), "notes": {"team_lesson": text}}
                if invocation.purpose == Purpose.RETROSPECTIVE:
                    return {"lessons": [], "procedures": [], "skill_proposals": []}
                return None

            result = ApiRunner(company, ScriptedClient(notes), routes).run("Learn from a report")
            assert result.status == "done"
        pending = company.company.pending_memory("department", "engineering")
        assert len(pending) == 1 and pending[0].learned_count == 2
        shown: list[Any] = []

        def adopt(invocation: Any, state: Any, org_: Org) -> Any:
            if invocation.purpose == Purpose.DISPATCH:
                shown.extend(company.briefing(state.run_id, invocation).pending_lessons)
                return {**default_payload(invocation, state, org_), "adopt_lessons": [pending[0].id]}
            if invocation.purpose == Purpose.RETROSPECTIVE:
                return {"lessons": [], "procedures": [], "skill_proposals": []}
            return None

        assert ApiRunner(company, ScriptedClient(adopt), routes).run("Adopt the lesson").status == "done"
        assert [p["lesson"] for p in shown] == [FIRST]
        team_writes = [w for w in backend.writes if w[0] == "dept-engineering"]
        assert [w[1] for w in team_writes] == [FIRST, PARAPHRASE]
        assert all(e.source_run in w[2] for e, w in zip(pending[0].evidence, team_writes, strict=True))
        assert ApiRunner(company, ScriptedClient(notes), routes).run("Learn it again").status == "done"
        assert company.company.pending_memory("department", "engineering") == []
        assert [w[1] for w in backend.writes if w[0] == "dept-engineering"] == [FIRST, PARAPHRASE, PARAPHRASE]
    finally:
        company.close()


def test_a_dropped_merged_team_lesson_carries_both_sources_to_the_person(org: Org, tmp_path: Path) -> None:
    company = Orchestrator(org, tmp_path / "home", memory=MemoryGateway(org, ProposalBackend()))
    try:
        first, _, _ = learn(company.company, FIRST, "run-1", "department", "engineering")
        learn(company.company, PARAPHRASE, "run-2", "department", "engineering")

        def drop(invocation: Any, state: Any, org_: Org) -> Any:
            if invocation.purpose == Purpose.DISPATCH:
                return {**default_payload(invocation, state, org_), "drop_lessons": [first.id]}
            if invocation.purpose == Purpose.RETROSPECTIVE:
                return {"lessons": [], "procedures": [], "skill_proposals": []}
            return None

        routes = ModelRoutes({"standard": "m", "executive": "m", "fast": "m"})
        assert ApiRunner(company, ScriptedClient(drop), routes).run("Decide the team lesson").status == "done"
        waiting = company.company.pending_memory("company")
        assert len(waiting) == 1 and waiting[0].learned_count == 2
        assert [e.source_run for e in waiting[0].evidence] == ["run-1", "run-2"]
        assert [e.text for e in waiting[0].evidence] == [FIRST, PARAPHRASE]
        assert all("dropped by engineering/" in e.source and "artifact-1" in e.source for e in waiting[0].evidence)
    finally:
        company.close()


@pytest.mark.model
def test_the_real_local_model_reproduces_the_threshold_measurement() -> None:
    texts = list(MEASURED["vectors"])
    actual = FastEmbedEmbedder().embed_documents(texts)
    for pair in MEASURED["pairs"]:
        score = float(actual[texts.index(pair["first"])] @ actual[texts.index(pair["second"])])
        assert score == pytest.approx(pair["score"], abs=1e-5)
        assert (score >= LESSON_SIMILARITY[DEFAULT_MODEL]) == (pair["kind"] == "paraphrase")


def run_local_memory(tmp_path: Path, body: str) -> None:
    script = tmp_path / "memory_case.py"
    script.write_text(
        textwrap.dedent("""\
        import json, sys
        from pathlib import Path
        from try_professional.memory import MemorySettings, MnemosyneBackend, MemoryGateway
        from try_professional.org import load_org
        from try_professional.packs import available_packs, install_org
        work = Path(sys.argv[1])
        install_org(work / "org", "Test", available_packs())
        org = load_org(work / "org")
        backend = MnemosyneBackend(MemorySettings(data_dir=work / "memory"))
        gateway = MemoryGateway(org, backend)
        measured = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        first = measured["pairs"][0]["first"]
        paraphrase = measured["pairs"][0]["second"]
        distinct = measured["pairs"][1]["second"]
        """)
        + textwrap.dedent(body),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(script), str(tmp_path), str(DATA)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=50,
        env={**os.environ, "OMP_NUM_THREADS": "2"},
    )
    assert result.returncode == 0, result.stdout + result.stderr[-3000:]


@pytest.mark.model
def test_real_company_and_personal_memory_merge_across_runs_preserve_evidence_and_appear_once_in_briefs(
    tmp_path: Path,
) -> None:
    run_local_memory(
        tmp_path,
        """
        from try_professional.adapters.api_runner.scripted import default_payload
        from try_professional.adapters.mcp import McpService
        from try_professional.core.orchestrator import Orchestrator
        from try_professional.core.pipeline import Purpose
        role = 'engineering/backend'
        head = org.departments['engineering'].head_ref
        for text, source in ((first, 'run-1'), (paraphrase, 'run-2'), (first, 'run-3')):
            gateway.promote(org.root_role, text, source)
            gateway.write(role, text, source=source)
            gateway.write_department(head, text, source=source)
        for bank in ('company', 'agent-engineering--backend', 'dept-engineering'):
            assert backend.size(bank)['entries'] == 1
            lesson = json.loads(backend._bank(bank).conn.execute('SELECT metadata_json FROM working_memory').fetchone()[0])['trypro_lesson']
            assert lesson['learned_count'] == 3
            assert [e['text'] for e in lesson['evidence']] == [first, paraphrase, first]
        memory = backend._bank('company')
        row = memory.conn.execute('SELECT id,content,source,metadata_json FROM working_memory').fetchone()
        metadata = json.loads(row[3])['trypro_lesson']
        assert row[1] == first and 'run-1' in row[2]
        assert metadata['learned_count'] == 3
        assert [e['text'] for e in metadata['evidence']] == [first, paraphrase, first]
        assert [e['source'] for e in metadata['evidence']] == [f'{org.root_role} from run-{n}' for n in range(1,4)]
        assert json.loads(memory.conn.execute('SELECT metadata_json FROM memories WHERE id=?', (row[0],)).fetchone()[0])['trypro_lesson'] == metadata
        assert gateway.recall(role, first) == ['[personal, dept-engineering, company] ' + first]
        company = Orchestrator(org, work / 'home', memory=gateway)
        try:
            service = McpService(company)
            current = service.run_start('Review cited files')
            run_id = current['run_id']
            for _ in range(4):
                action = current['actions'][0]
                if action['purpose'] == 'execute':
                    assert action['role'] == role
                    assignment = McpService(company, bound_role=role).run_next_action(run_id)['actions'][0]['assignment']
                    assert '## Relevant memory' in assignment
                    assert assignment.count(first) == 1 and paraphrase not in assignment
                    assert '[personal, dept-engineering, company] ' + first in assignment
                    break
                state = company.state(run_id)
                invocation = state.invocations[action['invocation_id']]
                current = service.artifact_submit(run_id, action['invocation_id'], default_payload(invocation, state, org))
                assert current['accepted'], current
            else:
                raise AssertionError('the task was never delivered')
            invocation = next(iter(company.state(run_id).invocations.values()))
            invocation = invocation.model_copy(update={'purpose': Purpose.ESCALATION})
            assert company.briefing(run_id, invocation).brief.state.count(first) == 1
        finally:
            company.close()
        gateway.promote(org.root_role, distinct, 'run-4')
        assert backend.size('company')['entries'] == 2
        reopened = MnemosyneBackend(backend.settings)
        assert reopened.size('company')['entries'] == 2
        assert reopened.recall('company', first, 5)[0].text == first
    """,
    )


@pytest.mark.model
def test_real_team_memory_keeps_evidence_from_an_old_entry_and_serializes_concurrent_learning(tmp_path: Path) -> None:
    run_local_memory(
        tmp_path,
        """
        from concurrent.futures import ThreadPoolExecutor
        head = org.departments['engineering'].head_ref
        memory = backend._bank('dept-engineering')
        old_id = memory.remember(first, source='old-run', scope='global')
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda n: gateway.write_department(head, paraphrase, source=f'run-{n}'), (2, 3)))
        assert backend.size('dept-engineering')['entries'] == 1
        row = memory.conn.execute('SELECT id,content,metadata_json FROM working_memory').fetchone()
        lesson = json.loads(row[2])['trypro_lesson']
        assert row[0] == old_id and row[1] == first and lesson['learned_count'] == 3
        assert lesson['evidence'][0] == {'text': first, 'source': 'old-run'}
        assert {e['source'] for e in lesson['evidence'][1:]} == {f'{head} from run-2', f'{head} from run-3'}
        gateway.write_department(head, distinct, source='run-4')
        gateway.promote(org.root_role, paraphrase, 'another-bank')
        assert backend.size('dept-engineering')['entries'] == 2
        assert backend.size('company')['entries'] == 1
    """,
    )
