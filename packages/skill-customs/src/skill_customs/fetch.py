"""Download by full commit SHA, and extract without trusting the archive. No clone, so no git hooks."""

from __future__ import annotations

import json
import os
import re
import shutil
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .errors import FetchError, UnsafeArchive
from .policy import Policy

API = "https://api.github.com"
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))}
_CHUNK = 1024 * 256


def is_full_sha(value: str) -> bool:
    return bool(_FULL_SHA.match(value))


def check_repo(repo: str) -> None:
    if not _REPO.match(repo) or ".." in repo:
        raise FetchError(f"'{repo}' isn't an owner/name repository")


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub redirects downloads to another host, which must never receive the token."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            new.headers.pop("Authorization", None)
            new.unredirected_hdrs.pop("Authorization", None)
        return new


class GitHubClient:
    """Read-only GitHub access. The token is optional, and is read from GITHUB_TOKEN by default."""

    def __init__(self, token: str | None = None, *, opener: Any = None) -> None:
        self._token = token if token is not None else os.environ.get("GITHUB_TOKEN")
        self._opener = opener or urllib.request.build_opener(_DropAuthOnRedirect())

    def _request(self, url: str) -> Any:
        request = urllib.request.Request(
            url, headers={"User-Agent": "skill-customs", "Accept": "application/vnd.github+json"}
        )
        if self._token:
            request.add_header("Authorization", f"Bearer {self._token}")
        try:
            return self._opener.open(request, timeout=120)
        except urllib.error.HTTPError as error:
            hint = " Rate limited: set GITHUB_TOKEN." if error.code in (403, 429) else ""
            raise FetchError(
                f"GitHub returned HTTP {error.code} for {urllib.parse.urlsplit(url).path}.{hint}"
            ) from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise FetchError(f"Could not reach GitHub: {type(error).__name__}") from None

    def get_json(self, path: str) -> Any:
        with self._request(f"{API}{path}") as response:
            return json.loads(response.read().decode("utf-8"))

    def resolve_ref(self, repo: str, ref: str) -> str:
        """Turn a branch or tag into a SHA. Nothing else in this package accepts a ref."""
        check_repo(repo)
        data = self.get_json(f"/repos/{repo}/commits/{urllib.parse.quote(ref, safe='')}")
        sha = data.get("sha", "")
        if not is_full_sha(sha):
            raise FetchError(f"GitHub didn't return a commit for {repo}@{ref}")
        return str(sha)

    def download_tarball(self, repo: str, commit: str, target: Path, policy: Policy) -> None:
        check_repo(repo)
        if not is_full_sha(commit):
            raise FetchError(f"'{commit}' isn't a full 40-character commit SHA. Branches and tags aren't accepted.")
        total = 0
        with self._request(f"{API}/repos/{repo}/tarball/{commit}") as response, target.open("wb") as out:
            while chunk := response.read(_CHUNK):
                total += len(chunk)
                if total > policy.max_archive_bytes:
                    raise UnsafeArchive(f"The archive is over {policy.max_archive_bytes} bytes")
                out.write(chunk)


def _safe_parts(name: str) -> list[str]:
    if "\\" in name or "\0" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise UnsafeArchive(f"Unsafe path in archive: {name!r}")
    parts = [part for part in name.split("/") if part and part != "."]
    for part in parts:
        if part == ".." or ":" in part or part.split(".")[0].lower() in _WINDOWS_RESERVED or part.endswith((" ", ".")):
            raise UnsafeArchive(f"Unsafe path in archive: {name!r}")
    return parts


def extract_skill(archive: Path, subpath: str, commit: str, target: Path, policy: Policy) -> Path | None:
    """Extract one folder by writing bytes ourselves, with no tarfile.extract. Returns the repo root copy.

    Links anywhere in the requested folder are refused, not skipped, because a skill that
    ships one isn't what it appears to be. Top-level license files are kept for the license gate.
    """
    wanted = [part for part in subpath.strip("/").split("/") if part]
    if ".." in wanted:
        raise UnsafeArchive(f"Unsafe skill path: {subpath!r}")
    files = 0
    total = 0
    found = False
    repo_root = target.parent / f"{target.name}-repo-root"
    repo_root.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            parts = _safe_parts(member.name)
            if len(parts) < 2:
                _check_top_folder(parts, commit)
                continue
            _check_top_folder(parts[:1], commit)
            inner = parts[1:]
            is_license = len(inner) == 1 and inner[0].upper().startswith(("LICENSE", "LICENCE", "COPYING"))
            if is_license and member.isfile() and member.size <= policy.max_file_bytes:
                handle = tar.extractfile(member)
                if handle is not None:
                    (repo_root / inner[0]).write_bytes(handle.read())
            if inner[: len(wanted)] != wanted:
                continue
            relative = inner[len(wanted) :]
            if not relative:
                if member.isdir():
                    found = True
                    continue
                raise UnsafeArchive(f"'{subpath}' isn't a folder")
            if member.issym() or member.islnk():
                raise UnsafeArchive(f"The skill contains a link: {'/'.join(relative)}")
            if member.isdir():
                found = True
                continue
            if not member.isfile():
                raise UnsafeArchive(f"The skill contains a special file: {'/'.join(relative)}")
            found = True
            files += 1
            total += member.size
            if files > policy.max_files or total > policy.max_skill_bytes:
                raise UnsafeArchive("The skill is too large")
            destination = target.joinpath(*relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                raise UnsafeArchive(f"Can't read {'/'.join(relative)}")
            with destination.open("wb") as out:
                shutil.copyfileobj(source, out)
    if not found and wanted:
        raise FetchError(f"'{subpath}' isn't in this commit")
    return repo_root


def _check_top_folder(parts: list[str], commit: str) -> None:
    """GitHub names the top folder owner-repo-<short sha>. A mismatch means the wrong content."""
    if parts and not parts[0].rsplit("-", 1)[-1].startswith(commit[:7]):
        raise UnsafeArchive("The archive doesn't match the requested commit")
