"""One embedding process for a company. It runs on a stand-in model, so these need no download."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from multiprocessing.connection import Client
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from try_professional.memory import embed_service, embedding
from try_professional.memory.embed_service import ADDRESS_FILE, ServiceModel, serve, service_is_off


class StandIn:
    def __init__(self) -> None:
        self.loaded = 0

    def embed(self, texts: list[str], batch_size: int = 8) -> list[Any]:
        return [np.array([float(len(text)), 1.0, 2.0], dtype=np.float32) for text in texts]


@pytest.fixture
def running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    stand_in = StandIn()
    monkeypatch.setattr(embed_service, "shared_model", lambda name, cache_dir=None: stand_in)
    monkeypatch.setattr(embed_service.os, "_exit", lambda code: None)
    thread = threading.Thread(target=serve, args=(tmp_path,), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not (tmp_path / ADDRESS_FILE).is_file():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    yield tmp_path


class TestTheService:
    def test_it_turns_text_into_vectors(self, running: Path) -> None:
        vectors = ServiceModel(running, "m").embed(["hi", "there"])
        assert [float(v[0]) for v in vectors] == [2.0, 5.0]
        assert vectors[0].dtype == np.float32

    def test_a_single_string_works_as_for_a_real_model(self, running: Path) -> None:
        assert float(ServiceModel(running, "m").embed("hello")[0][0]) == 5.0

    def test_several_clients_share_it(self, running: Path) -> None:
        results: dict[int, float] = {}

        def ask(number: int) -> None:
            results[number] = float(ServiceModel(running, "m").embed(["x" * number])[0][0])

        threads = [threading.Thread(target=ask, args=(number,)) for number in range(1, 9)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert results == {number: float(number) for number in range(1, 9)}

    def test_it_listens_on_this_machine_only(self, running: Path) -> None:
        address = json.loads((running / ADDRESS_FILE).read_text(encoding="utf-8"))
        assert address["host"] == "127.0.0.1"

    def test_it_refuses_a_client_without_the_key(self, running: Path) -> None:
        address = json.loads((running / ADDRESS_FILE).read_text(encoding="utf-8"))
        with pytest.raises(Exception):  # noqa: B017  # The handshake fails in a few ways, and all are refusals.
            Client((address["host"], address["port"]), authkey=b"wrong")
        assert float(ServiceModel(running, "m").embed(["still fine"])[0][0]) == 10.0

    def test_a_bad_request_doesnt_stop_it(self, running: Path) -> None:
        address = json.loads((running / ADDRESS_FILE).read_text(encoding="utf-8"))
        connection = Client((address["host"], address["port"]), authkey=address["key"].encode("ascii"))
        connection.send_bytes(b"this isn't json")
        connection.close()
        assert float(ServiceModel(running, "m").embed(["abc"])[0][0]) == 3.0

    def test_it_takes_no_python_objects(self) -> None:
        """A pickled request could run code in the process every role shares."""
        source = Path(embed_service.__file__).read_text(encoding="utf-8")
        assert ".recv()" not in source and ".send(" not in source and "pickle.load" not in source


class TestItCantLeakBetweenRoles:
    def test_it_knows_nothing_about_memory_skills_or_roles(self) -> None:
        source = Path(embed_service.__file__).read_text(encoding="utf-8")
        for word in ("gateway", "MnemosyneBackend", "SkillCatalog", "load_org", "mnemosyne.db"):
            assert word not in source
        imports = [line for line in source.splitlines() if line.startswith(("from ", "import "))]
        assert not [line for line in imports if "memory" in line or "skills" in line or "org" in line]

    def test_a_role_still_only_reads_its_own_memory(self, tmp_path: Path) -> None:
        from try_professional.memory import MemoryGateway
        from try_professional.org import load_org
        from try_professional.packs import available_packs, install_org

        from .test_company import Backend

        install_org(tmp_path / "org", "Test", available_packs())
        gateway = MemoryGateway(load_org(tmp_path / "org"), Backend())
        gateway.write("engineering/backend", "A private note")
        assert not gateway.search("marketing/seo", "note")
        assert gateway.search("engineering/backend", "note")


class TestUsingIt:
    def test_a_process_set_to_use_it_holds_no_model(self, running: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(embedding, "_models", {})
        monkeypatch.setattr(embedding, "_service_home", None)
        embedding.use_embedding_service(running)
        try:
            model = embedding.shared_model("m")
            assert isinstance(model, ServiceModel)
            assert float(model.embed(["abcd"])[0][0]) == 4.0
        finally:
            embedding._service_home = None

    def test_it_can_be_turned_off(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRYPRO_EMBED_SERVICE", "off")
        monkeypatch.setattr(embedding, "_service_home", None)
        assert service_is_off()
        embedding.use_embedding_service(tmp_path)
        assert embedding._service_home is None

    def test_if_it_cant_be_reached_a_role_still_gets_its_memory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It costs memory to load a copy, and that's better than a role with no memory at all."""
        monkeypatch.setattr(embed_service, "shared_model", lambda name, cache_dir=None: StandIn())

        def cant_start(home: Path, cache_dir: Any) -> None:
            raise embed_service.ServiceUnavailable("no")

        monkeypatch.setattr(embed_service, "_start", cant_start)
        assert float(ServiceModel(tmp_path, "m").embed(["abc"])[0][0]) == 3.0

    def test_an_old_address_file_doesnt_break_it(self, tmp_path: Path) -> None:
        (tmp_path / ADDRESS_FILE).write_text("not json", encoding="utf-8")
        assert embed_service._read_address(tmp_path) is None
        assert embed_service._connect_or_none(tmp_path) is None
