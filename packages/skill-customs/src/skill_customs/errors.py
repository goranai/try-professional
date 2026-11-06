"""Errors. Messages are safe to print, and never contain a token."""

from __future__ import annotations

from typing import Any


class SkillCustomsError(Exception):
    """Base class for anything this package refuses to do."""


class NotASkill(SkillCustomsError):
    """The folder has no SKILL.md."""


class FetchError(SkillCustomsError):
    """A download failed, or a ref or repository name was invalid."""


class UnsafeArchive(SkillCustomsError):
    """The archive tried something a skill never needs: a link, a path escape, or too much data."""


class LockfileError(SkillCustomsError):
    """skills.lock is missing something, or is from an unknown version."""


class ApprovalError(SkillCustomsError):
    """An approval can't be recorded."""


class LicenseRejected(SkillCustomsError):
    """The license isn't on the allowlist, or couldn't be determined."""

    def __init__(self, message: str, verdict: Any) -> None:
        self.verdict = verdict
        super().__init__(message)
