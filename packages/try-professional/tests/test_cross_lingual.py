"""Cross-lingual retrieval with the real model and real Mnemosyne. Runs with: pytest -m model

The target is recall@5 of at least 80% in every language on the heldout split, set
before the first measurement. A language that misses it is marked xfail with a reason,
so the gap stays visible rather than the target quietly moving.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from try_professional.bench.retrieval.evaluate import LANGUAGES, evaluate, load_catalog, load_queries
from try_professional.memory import FastEmbedEmbedder, score_floor
from try_professional.memory.embedding import DEFAULT_MODEL
from try_professional.org import Org
from try_professional.skills import SkillCatalog, SkillRecord

pytestmark = pytest.mark.model
TARGET = 0.80
KNOWN_GAPS = {
    "en": "14/20 on heldout. The English queries are the most idiomatic in the set, and paraphrase far from the catalog."
}


@pytest.fixture(scope="module")
def catalog(tmp_path_factory: pytest.TempPathFactory) -> Iterator[SkillCatalog]:
    built = SkillCatalog(tmp_path_factory.mktemp("catalog") / "c.sqlite", FastEmbedEmbedder())
    built.rebuild([SkillRecord(s["id"], s["id"], s["description"], "benchmark") for s in load_catalog()])
    yield built
    built.close()


def _rank(catalog: SkillCatalog) -> Any:
    def rank(query: str) -> list[tuple[str, float]]:
        return [(h.record.skill_id, h.score) for h in catalog.search(query, top_k=5)]

    return rank


@pytest.mark.parametrize(
    "language",
    [
        pytest.param(lang, marks=pytest.mark.xfail(reason=KNOWN_GAPS[lang], strict=True))
        if lang in KNOWN_GAPS
        else lang
        for lang in LANGUAGES
    ],
)
def test_heldout_recall_meets_the_target(catalog: SkillCatalog, language: str) -> None:
    result = evaluate(_rank(catalog), "heldout", score_floor(DEFAULT_MODEL))[language]
    assert result.total == 20
    assert result.recall_at_5 >= TARGET, result.misses


def test_overall_recall_does_not_regress(catalog: SkillCatalog) -> None:
    results = evaluate(_rank(catalog), "heldout", score_floor(DEFAULT_MODEL))
    assert sum(r.top5 for r in results.values()) >= 88


def test_the_same_skill_is_found_from_all_five_languages(catalog: SkillCatalog) -> None:
    rank = _rank(catalog)
    for skill in ("privacy-policy", "market-sizing", "support-macro"):
        for language in LANGUAGES:
            queries = [q for q in load_queries(language)["queries"] if q["skill"] == skill]
            assert any(skill in [s for s, _ in rank(q["text"])] for q in queries), (skill, language)


# Mnemosyne reads some settings once at import, so each of these runs in a fresh process.
def _run_isolated(tmp_path: Path, body: str) -> None:
    script = tmp_path / "isolated.py"
    script.write_text(
        textwrap.dedent(
            """\
            import socket, sys
            from pathlib import Path
            from try_professional.memory import MemorySettings, MnemosyneBackend, MemoryGateway
            from try_professional.org import load_org
            from try_professional.packs import available_packs, install_org
            work = Path(sys.argv[1])
            install_org(work / "org", "Test", available_packs())
            org = load_org(work / "org")
            backend = MnemosyneBackend(MemorySettings(data_dir=work / "memory"))
            gateway = MemoryGateway(org, backend)
            """
        )
        + textwrap.dedent(body),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(script), str(tmp_path)], capture_output=True, text=True, encoding="utf-8", timeout=300
    )
    assert result.returncode == 0, result.stderr[-1500:]


def test_memory_written_in_one_language_is_found_from_others(tmp_path: Path) -> None:
    _run_isolated(
        tmp_path,
        """
        gateway.write("engineering/backend", "Peluncuran produk dijadwalkan pada hari Jumat setelah pengujian selesai.")
        for query in ("when is the product launch scheduled", "¿cuándo es el lanzamiento del producto?", "产品什么时候发布"):
            hits = gateway.search("engineering/qa", query)
            assert any("Peluncuran" in h.text for h in hits), query
        assert gateway.search("marketing/social", "when is the product launch scheduled") == []
        """,
    )


def test_memories_do_not_expire_after_a_week(tmp_path: Path) -> None:
    _run_isolated(
        tmp_path,
        """
        import sqlite3
        gateway.write("sales/sales-lead", "The enterprise client prefers annual billing.")
        path = work / "memory" / "banks" / "dept-sales" / "mnemosyne.db"
        conn = sqlite3.connect(path)
        conn.execute("UPDATE working_memory SET timestamp = '2020-01-01T00:00:00'")
        conn.commit()
        conn.close()
        for n in range(3):
            gateway.write("sales/sales-lead", f"Filler note {n}, which triggers the trim.")
        hits = gateway.search("sales/sales-lead", "what billing does the enterprise client prefer")
        assert any("annual billing" in h.text for h in hits), [h.text for h in hits]
        """,
    )


def test_recall_makes_no_network_calls_even_with_other_keys_set(tmp_path: Path) -> None:
    _run_isolated(
        tmp_path,
        """
        import os
        os.environ["OPENAI_API_KEY"] = "never-use-this-value"
        os.environ["HF_HUB_OFFLINE"] = "1"
        gateway.write("engineering/backend", "warm up the model while the network is allowed")
        gateway.search("engineering/backend", "warm up")

        def refuse(*args, **kwargs):
            raise AssertionError("memory tried to use the network")

        socket.socket.connect = refuse
        socket.create_connection = refuse
        gateway.write("engineering/backend", "The staging database is rotated monthly.")
        assert any("rotated" in h.text for h in gateway.search("engineering/qa", "basis data staging dirotasi"))
        """,
    )


def test_each_bank_is_its_own_file_and_nothing_lands_in_the_hermes_home(tmp_path: Path) -> None:
    _run_isolated(
        tmp_path,
        """
        gateway.write("engineering/backend", "engineering note")
        gateway.promote("executive/ceo", "company note", "art-1")
        banks = sorted(p.parent.name for p in (work / "memory" / "banks").glob("*/mnemosyne.db"))
        assert banks == ["company", "dept-engineering"], banks
        """,
    )


def test_real_gateway_keeps_departments_apart(org: Org, tmp_path: Path) -> None:
    _ = org
    _run_isolated(
        tmp_path,
        """
        gateway.write("legal/counsel", "Contract renewal for the Jakarta office is due in June.")
        assert gateway.search("legal/compliance-reviewer", "contract renewal Jakarta")
        assert gateway.search("marketing/social", "contract renewal Jakarta") == []
        assert socket is not None
        """,
    )


def test_a_later_run_recalls_what_an_earlier_run_learned_across_languages(tmp_path: Path) -> None:
    """The whole loop on the real stack, with a scripted model: lesson, company memory, next run."""
    _run_isolated(
        tmp_path,
        """
        from try_professional.adapters.api_runner.runner import ApiRunner, ModelRoutes
        from try_professional.adapters.api_runner.scripted import ScriptedClient
        from try_professional.core.orchestrator import Orchestrator
        from try_professional.core.pipeline import Purpose

        LESSON = "Selalu tulis pengujian otomatis untuk setiap endpoint health check sebelum rilis."

        def teach(invocation, state, org_):
            if invocation.purpose == Purpose.RETROSPECTIVE:
                return {"lessons": [LESSON], "procedures": [], "skill_proposals": []}
            return None

        routes = ModelRoutes({"standard": "m", "executive": "m", "fast": "m"})
        orchestrator = Orchestrator(org, work / "home", memory=gateway)
        first = ApiRunner(orchestrator, ScriptedClient(teach), routes).run("Tambahkan endpoint health check")
        assert first.status == "done"
        second = ApiRunner(orchestrator, ScriptedClient(), routes).run("Add automated tests for the health check endpoint")
        assert second.status == "done"
        task = next(t for t in orchestrator.state(second.run_id).tasks.values() if t.work_package_id)
        assert any(LESSON in m and m.startswith("[company]") for m in task.memory_context), task.memory_context
        orchestrator.close()
        """,
    )


def test_the_command_line_builds_real_memory(tmp_path: Path) -> None:
    _run_isolated(
        tmp_path,
        """
        from try_professional.cli import _memory
        from try_professional.config import load_config, CONFIG_TEMPLATE
        (work / "trypro.yaml").write_text(CONFIG_TEMPLATE.replace(".trypro", "memory-home"), encoding="utf-8")
        config = load_config(work)
        assert config.memory.enabled and not config.memory.remote_allowed
        built = _memory(config, org)
        assert built.floor > 0
        built.write("sales/sales-lead", "note")
        config.memory.enabled = False
        assert _memory(config, org) is None
        """,
    )
