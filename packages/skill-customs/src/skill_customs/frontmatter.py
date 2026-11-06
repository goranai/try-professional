"""Read a skill's metadata. Everything in it is untrusted text."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import NotASkill

SKILL_FILE = "SKILL.md"


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    license: str | None
    category: str | None
    tools: tuple[str, ...] = ()


def _nested_category(data: dict[str, Any]) -> str | None:
    """Skills declare a category at the top level, or under metadata.<framework>.category."""
    if isinstance(data.get("category"), str):
        return str(data["category"])
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        for value in metadata.values():
            if isinstance(value, dict) and isinstance(value.get("category"), str):
                return str(value["category"])
    return None


def _declared_tools(data: dict[str, Any]) -> tuple[str, ...]:
    """Frameworks spell this differently, as a list or as a space or comma separated string."""
    for key in ("allowed-tools", "allowed_tools", "tools"):
        value = data.get(key)
        if isinstance(value, list):
            return tuple(str(item) for item in value)
        if isinstance(value, str):
            return tuple(part for part in value.replace(",", " ").split() if part)
    return ()


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Return the YAML block at the top of a file, or nothing. safe_load runs no code."""
    stripped = text.lstrip("﻿")
    if not stripped.startswith("---"):
        return {}
    parts = stripped.split("\n---", 1)
    if len(parts) != 2:
        return {}
    try:
        loaded = yaml.safe_load(parts[0][3:])
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def read_skill_metadata(skill: Path) -> SkillMetadata:
    path = skill / SKILL_FILE
    if not path.is_file():
        raise NotASkill(f"{skill} has no {SKILL_FILE}")
    data = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    license_value = data.get("license")
    return SkillMetadata(
        name=str(data.get("name") or skill.name),
        description=str(data.get("description") or ""),
        license=str(license_value) if license_value else None,
        category=_nested_category(data),
        tools=_declared_tools(data),
    )
