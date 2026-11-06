"""Load the management playbooks by role level and step. See playbooks/README.md."""

from __future__ import annotations

import hashlib
from functools import cache
from importlib import resources
from typing import Any

from .org import Org

NAMES = ("ceo", "manager", "staff", "memory-and-skills")
_POLICY = "memory-and-skills"
_POLICY_STEPS = frozenset({"plan", "dispatch", "execute"})


def _parse(text: str) -> tuple[int, dict[str, str]]:
    _, header, body = text.split("---", 2)
    version = next(int(line.split(":")[1]) for line in header.splitlines() if line.startswith("version:"))
    sections: dict[str, str] = {}
    for chunk in body.split("\n## ")[1:]:
        name, _, content = chunk.partition("\n")
        sections[name.strip()] = content.strip()
    return version, sections


@cache
def _load(name: str) -> tuple[int, dict[str, str], str]:
    root: Any = resources.files("try_professional._playbooks")
    raw = root.joinpath(f"{name}.md").read_bytes()
    version, sections = _parse(raw.decode("utf-8"))
    return version, sections, hashlib.sha256(raw).hexdigest()


def level_of(org: Org, role: str) -> str:
    if role == org.root_role:
        return "ceo"
    return "manager" if org.is_head(role) else "staff"


def section(level: str, step: str) -> str:
    """A step's instructions. A head doing a task follows the staff playbook for it."""
    _, sections, _ = _load(level)
    text = sections.get(step) or _load("staff")[1][step]
    if step in _POLICY_STEPS:
        text += "\n\n" + _load(_POLICY)[1][level]
    return text


def versions() -> dict[str, dict[str, Any]]:
    """Recorded with each run, so results can be traced to the playbooks that produced them."""
    return {name: {"version": _load(name)[0], "sha256": _load(name)[2]} for name in NAMES}
