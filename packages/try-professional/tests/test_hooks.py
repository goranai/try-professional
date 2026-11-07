"""The pre-commit hook must keep covering what has gone into a commit broken before."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("precommit_checks", ROOT / "scripts" / "precommit_checks.py")
assert spec is not None and spec.loader is not None
precommit: Any = importlib.util.module_from_spec(spec)
sys.modules["precommit_checks"] = precommit
spec.loader.exec_module(precommit)


def test_every_test_it_leaves_out_exists() -> None:
    """A renamed file would otherwise quietly come back into the fast set, or stay out of it for no reason."""
    for name in precommit.SLOW_TESTS:
        assert (ROOT / precommit.TESTS / name).is_file(), name


def test_it_keeps_the_tests_that_commits_have_broken() -> None:
    """Commits have gone in over the prompt budget. test_token_budget, test_loadouts and test_company check it."""
    for name in (
        "test_company.py",
        "test_loadouts.py",
        "test_token_budget.py",
        "test_pipeline.py",
        "test_misbehaving_model.py",
        "test_spec.py",
    ):
        assert name not in precommit.SLOW_TESTS


def test_it_runs_lint_types_and_tests() -> None:
    assert [name for name, _ in precommit.checks()] == ["ruff check", "ruff format", "mypy", "fast tests"]


def test_both_hooks_are_installed() -> None:
    text = (ROOT / "scripts" / "install_hooks.py").read_text(encoding="utf-8")
    assert '"pre-commit", "pre-push"' in text
    assert (ROOT / "scripts" / "hooks" / "pre-commit").is_file()
