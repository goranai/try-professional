from __future__ import annotations

import gzip
import io
import tarfile
from pathlib import Path
from typing import Any

import pytest

from skill_customs import GitHubClient, Policy

COMMIT = "a" * 40
MIT = "Permission is hereby granted, free of charge, to any person obtaining a copy of this software."
APACHE = "Apache License\nVersion 2.0, January 2004\n"
GPL = "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n"


def write_skill(
    root: Path, body: str = "Use this to review code.", *, license_name: str | None = "MIT", **extra: str
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    license_line = f"license: {license_name}\n" if license_name else ""
    (root / "SKILL.md").write_text(
        f"---\nname: {root.name}\ndescription: A skill\n{license_line}---\n{body}\n", encoding="utf-8"
    )
    for name, text in extra.items():
        target = root / name.replace("__", "/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def skill(tmp_path: Path) -> Path:
    return write_skill(tmp_path / "good-skill")


def build_tarball(
    members: dict[str, str | bytes] | None = None,
    *,
    top: str = f"owner-repo-{COMMIT[:7]}",
    links: dict[str, str] | None = None,
    raw_names: dict[str, str] | None = None,
) -> bytes:
    """Build a GitHub-style archive in memory. raw_names adds entries with exact, hostile names."""
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb") as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        root = tarfile.TarInfo(top + "/")
        root.type = tarfile.DIRTYPE
        tar.addfile(root)
        for name, content in (members or {}).items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(f"{top}/{name}")
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)
        for name, content in (raw_names or {}).items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class FakeOpener:
    """Stands in for the network. Records every request, and never leaves the process."""

    def __init__(self, tarball: bytes = b"", json_body: bytes = b"{}") -> None:
        self.tarball = tarball
        self.json_body = json_body
        self.requests: list[Any] = []

    def open(self, request: Any, timeout: float = 0) -> _Response:
        self.requests.append(request)
        return _Response(self.tarball if "/tarball/" in request.full_url else self.json_body)


def client_for(tarball: bytes) -> GitHubClient:
    return GitHubClient("", opener=FakeOpener(tarball))


@pytest.fixture
def policy() -> Policy:
    return Policy.default()
