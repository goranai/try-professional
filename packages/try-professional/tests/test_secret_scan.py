"""The pre-push scan: it must find secrets in the tree and in history, and never print them."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("secret_scan", ROOT / "scripts" / "secret_scan.py")
assert spec and spec.loader
secret_scan: Any = importlib.util.module_from_spec(spec)
sys.modules["secret_scan"] = secret_scan
spec.loader.exec_module(secret_scan)

# Built at runtime, so this file doesn't trip the scan it tests.
REAL_KEY = "plain" + "-looking-" + "value-" + "9f8e7d6c5b4a"
PATTERN_KEY = "sk-" + "a" * 32


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "first")
    return tmp_path


def _commit(repo: Path, name: str, text: str, message: str = "change") -> None:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)


def test_clean_repo_passes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert secret_scan.main([str(repo)]) == 0
    assert "clean" in capsys.readouterr().out


def test_ignored_env_file_is_fine_and_not_flagged(repo: Path) -> None:
    (repo / ".env").write_text(f"TRYPRO_LLM_API_KEY={REAL_KEY}\n", encoding="utf-8")
    assert secret_scan.main([str(repo)]) == 0


def test_exact_key_value_is_found_in_an_untracked_file(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / ".env").write_text(f"TRYPRO_LLM_API_KEY={REAL_KEY}\n", encoding="utf-8")
    (repo / "notes.md").write_text(f"remember {REAL_KEY}\n", encoding="utf-8")
    assert secret_scan.main([str(repo)]) == 1
    output = capsys.readouterr().out
    assert "notes.md:1" in output and "value-of-TRYPRO_LLM_API_KEY" in output


def test_key_removed_later_is_still_found_in_history(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / ".env").write_text(f"TRYPRO_LLM_API_KEY={REAL_KEY}\n", encoding="utf-8")
    _commit(repo, "config.py", f'KEY = "{REAL_KEY}"\n')
    _commit(repo, "config.py", "KEY = None\n")
    assert secret_scan.main([str(repo)]) == 1
    output = capsys.readouterr().out
    assert "history" in output and "config.py" in output


def test_key_in_a_commit_message_is_found(repo: Path) -> None:
    (repo / ".env").write_text(f"TRYPRO_LLM_API_KEY={REAL_KEY}\n", encoding="utf-8")
    _commit(repo, "b.txt", "x\n", message=f"oops {REAL_KEY}")
    assert secret_scan.main([str(repo)]) == 1


def test_known_key_patterns_are_found_with_no_env_file(repo: Path) -> None:
    _commit(repo, "b.txt", f"token {PATTERN_KEY}\n")
    assert secret_scan.main([str(repo)]) == 1


def test_a_tracked_env_file_fails_even_if_harmless(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / ".env").write_text("A=1\n", encoding="utf-8")
    _git(repo, "add", "-f", ".env")
    _git(repo, "commit", "-q", "-m", "oops")
    assert secret_scan.main([str(repo)]) == 1
    assert "tracked-env-file" in capsys.readouterr().out


def test_env_example_is_allowed(repo: Path) -> None:
    _commit(repo, ".env.example", "TRYPRO_LLM_API_KEY=your-key-here\n")
    assert secret_scan.main([str(repo)]) == 0


def test_the_output_never_contains_a_secret(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / ".env").write_text(f"TRYPRO_LLM_API_KEY={REAL_KEY}\n", encoding="utf-8")
    _commit(repo, "c.txt", f"{REAL_KEY} {PATTERN_KEY}\n")
    assert secret_scan.main([str(repo)]) == 1
    captured = capsys.readouterr()
    assert REAL_KEY not in captured.out + captured.err
    assert PATTERN_KEY not in captured.out + captured.err


def test_not_a_git_repo_fails_closed(tmp_path: Path) -> None:
    assert secret_scan.main([str(tmp_path)]) == 2
