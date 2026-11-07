"""Memory use: one copy of the model, nothing left unpacked, and no work started without room for it."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from try_professional import resources
from try_professional.cli import main
from try_professional.generator import activate, create_proposal, load_proposal
from try_professional.generator.provision import STALE_TEMP_SECONDS, remove_stale_temp_folders
from try_professional.memory import FastEmbedEmbedder, embedding
from try_professional.org import Org
from try_professional.resources import LowMemory, parse_meminfo, parse_vm_stat, stop_if_memory_low, wait_for_memory

from .conftest import Harness
from .test_memory import FakeEmbedder


@pytest.fixture
def checking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRYPRO_MEMORY_CHECK", "on")
    monkeypatch.delenv("TRYPRO_MEMORY_MARGIN_MB", raising=False)


@pytest.mark.usefixtures("checking")
class TestWaitingForMemory:
    def test_it_starts_when_theres_room_for_the_work_and_the_margin(self) -> None:
        slept: list[float] = []
        wait_for_memory(800, sleep=slept.append, free=lambda: 800 + resources.SAFETY_MARGIN_MB)
        assert slept == []

    def test_it_pauses_and_checks_again_until_theres_room(self) -> None:
        readings = iter([100, 900, 5000])
        slept: list[float] = []
        said: list[str] = []
        wait_for_memory(800, say=said.append, sleep=slept.append, free=lambda: next(readings), recheck_seconds=180)
        assert slept == [180, 180]
        assert "Nothing has started" in said[0]
        assert "continuing" in said[-1]

    def test_the_margin_can_be_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRYPRO_MEMORY_MARGIN_MB", "2000")
        readings = iter([2500, 2800])
        slept: list[float] = []
        wait_for_memory(800, sleep=slept.append, free=lambda: next(readings))
        assert len(slept) == 1

    def test_a_system_that_wont_say_doesnt_block_anyone(self) -> None:
        wait_for_memory(10**9, free=lambda: None, sleep=lambda s: pytest.fail("it waited"))

    def test_it_can_be_turned_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRYPRO_MEMORY_CHECK", "off")
        wait_for_memory(10**9, free=lambda: 1, sleep=lambda s: pytest.fail("it waited"))
        stop_if_memory_low(free=lambda: 1)

    def test_low_memory_stops_work_under_way_only_below_the_margin(self) -> None:
        stop_if_memory_low(free=lambda: resources.SAFETY_MARGIN_MB)
        with pytest.raises(LowMemory, match="safety margin"):
            stop_if_memory_low(free=lambda: resources.SAFETY_MARGIN_MB - 1)

    def test_this_system_reports_free_memory(self) -> None:
        free = resources.free_memory_mb()
        assert free is not None
        assert free > 0

    def test_linux_and_macos_readings(self) -> None:
        assert parse_meminfo("MemTotal: 8000000 kB\nMemAvailable:    2097152 kB\n") == 2048
        stat = "Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 1000.\nPages inactive: 1000.\n"
        assert parse_vm_stat(stat) == 31
        with pytest.raises(ValueError):
            parse_meminfo("MemTotal: 8000000 kB\n")


class TestOneCopyOfTheModel:
    def test_every_embedder_shares_one_copy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loads: list[str] = []

        class Model:
            def __init__(self, model_name: str, cache_dir: str) -> None:
                loads.append(model_name)

            def embed(self, texts: list[str], batch_size: int = 256) -> list[list[float]]:
                return [[1.0, 0.0] for _ in texts]

        monkeypatch.setattr(embedding, "_models", {})
        monkeypatch.setattr("fastembed.TextEmbedding", Model)
        assert not embedding.model_is_loaded("m")
        FastEmbedEmbedder("m", Path("c")).embed_query("one")
        FastEmbedEmbedder("m", Path("c")).embed_documents(["two"])
        list(embedding.SharedModel("m").embed(["three"]))
        assert loads == ["m"]
        assert embedding.model_is_loaded("m")

    def test_standing_in_for_a_model_loads_nothing_until_its_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(embedding, "_models", {})
        embedding.SharedModel("m")
        assert not embedding.model_is_loaded("m")

    @pytest.mark.model
    def test_company_memory_uses_the_same_copy(self, tmp_path: Path) -> None:
        """Measured: it was 1,183 MB with Mnemosyne holding a second copy. In its own process, as settings are."""
        code = """
import sys
from pathlib import Path
from try_professional.memory import FastEmbedEmbedder, MemorySettings, MnemosyneBackend, embedding
ours = FastEmbedEmbedder()
backend = MnemosyneBackend(MemorySettings(data_dir=Path(sys.argv[1])))
from mnemosyne.core import embeddings
ours.embed_query("ready")
backend.remember("company", "We ship on Mondays.", source="test")
hits = backend.recall("company", "When do we ship?", 3)
assert hits and hits[0].text == "We ship on Mondays."
assert isinstance(embeddings._embedding_model, embedding.SharedModel)
assert len(embedding._models) == 1
"""
        result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=55)
        assert result.returncode == 0, result.stderr[-600:]


class TestNothingLeftBehind:
    def test_only_our_old_temp_folders_are_removed(self, tmp_path: Path) -> None:
        names = ("trypro-skills-old", "skill-customs-old", "trypro-skills-new", "someone-elses")
        for name in names:
            (tmp_path / name).mkdir()
        now = 10.0**9
        old = now - STALE_TEMP_SECONDS - 1
        for name in ("trypro-skills-old", "skill-customs-old", "someone-elses"):
            os.utime(tmp_path / name, (old, old))
        os.utime(tmp_path / "trypro-skills-new", (now, now))
        remove_stale_temp_folders(tmp_path, now=now)
        assert sorted(p.name for p in tmp_path.iterdir()) == ["someone-elses", "trypro-skills-new"]

    def test_approving_a_proposal_deletes_what_it_unpacked(self, tmp_path: Path) -> None:
        bundle = create_proposal(tmp_path, FakeEmbedder(), template_id="marketing-agency", discovery=False)
        (bundle.path / "skills" / "someone" / "skill").mkdir(parents=True)
        assert (bundle.path / "catalog.sqlite").is_file()
        activate(bundle, tmp_path, FakeEmbedder(), by="a person")
        assert not (bundle.path / "skills").exists()
        assert not (bundle.path / "catalog.sqlite").exists()
        assert load_proposal(tmp_path, bundle.id).meta["status"] == "approved"


class TestGenerationsAndCommandsCheckFirst:
    def _recorded(self, monkeypatch: pytest.MonkeyPatch, module: str) -> list[int]:
        seen: list[int] = []
        monkeypatch.setattr(sys.modules[module], "wait_for_memory", lambda needed, **kw: seen.append(needed))
        return seen

    def test_a_generation_checks_before_it_does_anything(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = self._recorded(monkeypatch, "try_professional.generator.flow")
        monkeypatch.setattr(embedding, "_models", {})
        create_proposal(tmp_path, FakeEmbedder(), template_id="marketing-agency", discovery=False)
        assert seen == [resources.GENERATION_PEAK_MB]

    def test_a_generation_needs_less_once_the_model_is_loaded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self._recorded(monkeypatch, "try_professional.generator.flow")
        embedder = FakeEmbedder()
        monkeypatch.setattr(embedding, "_models", {embedder.model_name: object()})
        create_proposal(tmp_path, embedder, template_id="marketing-agency", discovery=False)
        assert seen == [resources.GENERATION_AFTER_MODEL_LOADED_MB]

    def test_a_command_checks_before_it_starts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = self._recorded(monkeypatch, "try_professional.cli")
        monkeypatch.chdir(tmp_path)
        assert main(["init", "--skip-model"]) == 0
        assert main(["run", "--dry-run", "Build a health check endpoint"]) == 0
        assert seen == [resources.COMMAND_PEAK_MB]


class TestNoOneAtTheKeyboard:
    def test_init_with_an_empty_stdin_says_so_and_doesnt_hang(self, tmp_path: Path) -> None:
        """Seen for real on Windows: isatty() is true for an empty stdin, and the prompt waited forever."""
        code = "import sys; from try_professional.cli import main; sys.exit(main(sys.argv[1:]))"
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                "init",
                "--template",
                "marketing-agency",
                "--no-discover",
                "--dir",
                str(tmp_path),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=50,
        )
        assert result.returncode == 3
        assert "needs a person at a terminal" in result.stdout


class TestStoppingAtASafePoint:
    def test_a_stopped_run_loses_nothing_and_resumes(
        self, org: Org, harness: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        running: Harness = harness(org)
        calls = {"n": 0}

        def low_after_two() -> None:
            calls["n"] += 1
            if calls["n"] > 2:
                raise LowMemory("Only 10 MB of memory is free.")

        monkeypatch.setattr("try_professional.adapters.api_runner.runner.stop_if_memory_low", low_after_two)
        run_id, _ = running.orchestrator.start("Build a health check endpoint")
        with pytest.raises(LowMemory):
            running.runner.resume(run_id)
        spent_before = running.orchestrator.state(run_id).usage.calls
        assert spent_before == 2

        monkeypatch.setattr("try_professional.adapters.api_runner.runner.stop_if_memory_low", lambda: None)
        result = running.runner.resume(run_id)
        assert result.status == "done"
        assert result.calls > spent_before

    def test_the_command_line_says_what_happened_and_how_to_continue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def low() -> None:
            raise LowMemory("Only 10 MB of memory is free.")

        monkeypatch.setattr("try_professional.adapters.api_runner.runner.stop_if_memory_low", low)
        monkeypatch.chdir(tmp_path)
        assert main(["init", "--skip-model"]) == 0
        assert main(["run", "--dry-run", "Build a health check endpoint"]) == 3
        output = capsys.readouterr().out
        assert "Only 10 MB of memory is free." in output
        assert "trypro resume" in output
