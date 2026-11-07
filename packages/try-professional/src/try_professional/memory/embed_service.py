"""One embedding process for a whole company, so 20 roles don't hold 20 copies of a 600 MB model.

The service only turns text into vectors. It can't read memory, skills, or anything about a role, so isolation between
roles stays where it was: in the memory rules, the skill loadouts, and each server's tool list. It listens on this machine
only, needs a key kept in the company's private folder, and speaks JSON and raw numbers, never pickle, so a connection
can't run code. It starts when first needed, and exits once it has been idle for a while.

If it can't start, a process loads its own copy, as before, so a problem here can't stop a company.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Iterable
from multiprocessing.connection import Client, Listener
from pathlib import Path
from typing import Any

import numpy as np

from .embedding import EMBED_BATCH_SIZE, shared_model

ADDRESS_FILE = "embed-service.json"
LOCK_FILE = "embed-service.lock"
IDLE_SECONDS = 600
START_TIMEOUT_SECONDS = 180
REQUEST_TIMEOUT_SECONDS = 300
OFF = "TRYPRO_EMBED_SERVICE"


class ServiceUnavailable(RuntimeError):
    pass


def _address_path(home: Path) -> Path:
    return home / ADDRESS_FILE


def _read_address(home: Path) -> dict[str, Any] | None:
    try:
        data: dict[str, Any] = json.loads(_address_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data


def _write_private(path: Path, text: str) -> None:
    """Written whole and swapped in, and readable only by this user, because it holds the key."""
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    temporary.replace(path)


def _serve_connection(connection: Any, seen: list[float], lock: threading.Lock, cache_dir: str | None) -> None:
    with connection:
        while True:
            try:
                request = json.loads(connection.recv_bytes())
            except (EOFError, OSError, ValueError):
                return
            seen[0] = time.monotonic()
            try:
                # This process is the service, so this is the real model, loaded on the first request.
                model = shared_model(str(request["model"]), Path(cache_dir) if cache_dir else None)
                # One at a time: it keeps peak memory at one batch, and this model is quick.
                with lock:
                    vectors = np.array(
                        list(model.embed(list(request["texts"]), batch_size=int(request["batch_size"]))),
                        dtype=np.float32,
                    )
                connection.send_bytes(json.dumps({"shape": list(vectors.shape)}).encode("utf-8"))
                connection.send_bytes(vectors.tobytes())
            except Exception as error:  # A bad request ends that request, and not the service every role depends on.
                connection.send_bytes(json.dumps({"error": type(error).__name__}).encode("utf-8"))
            seen[0] = time.monotonic()


def serve(home: Path, cache_dir: str | None = None, idle_seconds: float = IDLE_SECONDS) -> None:
    """Run the service until it has been idle for idle_seconds."""
    key = secrets.token_hex(32)
    home.mkdir(parents=True, exist_ok=True)
    seen = [time.monotonic()]
    lock = threading.Lock()
    with Listener(("127.0.0.1", 0), authkey=key.encode("ascii")) as listener:
        host, port = listener.address
        _write_private(_address_path(home), json.dumps({"host": host, "port": port, "key": key, "pid": os.getpid()}))

        def stop_when_idle() -> None:
            while time.monotonic() - seen[0] < idle_seconds:
                time.sleep(min(5.0, idle_seconds))
            _address_path(home).unlink(missing_ok=True)
            os._exit(0)

        threading.Thread(target=stop_when_idle, daemon=True).start()
        while True:
            try:
                connection = listener.accept()
            except OSError:  # A client with the wrong key is refused here, and the service carries on.
                continue
            threading.Thread(target=_serve_connection, args=(connection, seen, lock, cache_dir), daemon=True).start()


def _start(home: Path, cache_dir: Path | None) -> None:
    """Start the service unless another process just did. It's detached, so it outlives whichever role started it."""
    from ..core.lock import StateLock

    with StateLock(home, name=LOCK_FILE):
        if _connect_or_none(home) is not None:
            return
        _address_path(home).unlink(missing_ok=True)
        command = [sys.executable, "-m", "try_professional.memory.embed_service", str(home)]
        if cache_dir:
            command.append(str(cache_dir))
        flags: dict[str, Any] = {}
        if sys.platform == "win32":
            flags["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            flags["start_new_session"] = True
        # No shared handles: an MCP server's stdout is its protocol, and a client waits for that to close.
        subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags
        )
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if _connect_or_none(home) is not None:
                return
            time.sleep(0.2)
    raise ServiceUnavailable("The embedding service didn't start in time.")


def _connect_or_none(home: Path) -> Any:
    address = _read_address(home)
    if address is None:
        return None
    try:
        return Client((address["host"], int(address["port"])), authkey=str(address["key"]).encode("ascii"))
    except (OSError, EOFError, KeyError, ValueError):
        return None


class ServiceModel:
    """Looks like a fastembed model to its callers, including Mnemosyne, but holds no model itself."""

    def __init__(self, home: Path, model_name: str, cache_dir: Path | None = None) -> None:
        self.home = home
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._connection: Any = None
        self._lock = threading.Lock()

    def _connected(self) -> Any:
        if self._connection is None:
            self._connection = _connect_or_none(self.home)
        if self._connection is None:
            _start(self.home, self.cache_dir)
            self._connection = _connect_or_none(self.home)
        if self._connection is None:
            raise ServiceUnavailable("Couldn't reach the embedding service.")
        return self._connection

    def _ask(self, texts: list[str], batch_size: int) -> np.ndarray:
        request = json.dumps({"model": self.model_name, "texts": texts, "batch_size": batch_size})
        connection = self._connected()
        connection.send_bytes(request.encode("utf-8"))
        if not connection.poll(REQUEST_TIMEOUT_SECONDS):
            raise ServiceUnavailable("The embedding service stopped answering.")
        header = json.loads(connection.recv_bytes())
        if "error" in header:
            raise ServiceUnavailable(f"The embedding service failed: {header['error']}")
        vectors: np.ndarray = np.frombuffer(connection.recv_bytes(), dtype=np.float32).reshape(header["shape"])
        return vectors

    def embed(self, documents: Iterable[str] | str, batch_size: int = EMBED_BATCH_SIZE, **_: Any) -> list[Any]:
        texts = [documents] if isinstance(documents, str) else list(documents)
        with self._lock:
            try:
                vectors = self._ask(texts, batch_size)
            except (OSError, EOFError, ServiceUnavailable):
                # It may have just gone idle and exited, so try once more with a new one.
                self._connection = None
                try:
                    vectors = self._ask(texts, batch_size)
                except (OSError, EOFError, ServiceUnavailable):
                    self._connection = None
                    # A model of its own costs memory, and that's better than a role with no memory at all.
                    return list(shared_model(self.model_name, self.cache_dir).embed(texts, batch_size=batch_size))
        return list(vectors)


def service_is_off() -> bool:
    return os.environ.get(OFF, "").strip().lower() == "off"


if __name__ == "__main__":
    serve(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else None)
