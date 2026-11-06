"""Tools an API-run role can call. The role file decides which ones it gets.

Permissions are enforced here by the runner, whatever the model was persuaded to try.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...core.artifacts import PAYLOAD_TYPES, Flags
from ...core.ports import MemoryPort, SkillPort
from .clients import ToolSpec
from .prompts import untrusted

SUBMIT = "submit_artifact"
ESCALATE = "escalate"
HANDOFF = "request_handoff"
CONTROL_TOOLS = {SUBMIT, ESCALATE, HANDOFF}
MAX_FILE_BYTES = 2_000_000


class ToolRefused(Exception):
    """Refused for a policy reason. The model is told why, and the audit log records it."""


def _path_schema(description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"path": {"type": "string", "description": description}},
        "required": ["path"],
        "additionalProperties": False,
    }


_SPECS: dict[str, ToolSpec] = {
    "workspace_read": ToolSpec(
        "workspace_read", "Read a text file from this run's workspace.", _path_schema("Path relative to the workspace")
    ),
    "workspace_list": ToolSpec(
        "workspace_list",
        "List files in this run's workspace.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    "workspace_write": ToolSpec(
        "workspace_write",
        "Write a text file in this run's workspace.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the workspace"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    ),
    "memory_recall": ToolSpec(
        "memory_recall",
        "Search your department's memory and the company memory, in any language.",
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    "memory_write": ToolSpec(
        "memory_write",
        "Save a fact or lesson to your department's memory.",
        {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    ),
    "skill_search": ToolSpec(
        "skill_search",
        "Search vetted skills for this task, in any language.",
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
}

_ESCALATE_SPEC = ToolSpec(
    ESCALATE,
    "Send this item up the chain when you can't finish it or the decision isn't yours.",
    {
        "type": "object",
        "properties": {
            "reason": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}},
            "recommendation": {"type": "string"},
        },
        "required": ["reason"],
        "additionalProperties": False,
    },
)

_HANDOFF_SPEC = ToolSpec(
    HANDOFF,
    "Ask another department for work. Only department heads can. It is logged and waits for approval there.",
    {
        "type": "object",
        "properties": {
            "from_department": {"type": "string"},
            "to_department": {"type": "string"},
            "request": {"type": "string"},
            "acceptance": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "artifact_refs": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["from_department", "to_department", "request", "acceptance"],
        "additionalProperties": False,
    },
)


def submit_spec(output_type: str) -> ToolSpec:
    schema = PAYLOAD_TYPES[output_type].model_json_schema()
    definitions = schema.pop("$defs", {})
    wrapper: dict[str, Any] = {
        "type": "object",
        "properties": {
            "payload": schema,
            "flags": Flags.model_json_schema(),
            "language": {"type": "string", "description": "BCP 47 tag of this artifact's free text"},
        },
        "required": ["payload"],
        "additionalProperties": False,
    }
    if definitions:
        wrapper["$defs"] = definitions
    return ToolSpec(SUBMIT, f"Submit your finished {output_type}. This ends your assignment.", wrapper)


def tools_for(allowed: tuple[str, ...], output_type: str, *, is_head: bool) -> list[ToolSpec]:
    specs = [_SPECS[name] for name in allowed if name in _SPECS]
    specs += [submit_spec(output_type), _ESCALATE_SPEC]
    if is_head:
        specs.append(_HANDOFF_SPEC)
    return specs


@dataclass
class ToolContext:
    role: str
    allowed: tuple[str, ...]
    workspace: Path
    memory: MemoryPort
    skills: SkillPort


def _resolve(workspace: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ToolRefused("path must be a non-empty string")
    root = workspace.resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise ToolRefused(f"path '{relative}' is outside the workspace")
    return target


def run_tool(context: ToolContext, name: str, arguments: dict[str, Any]) -> str:
    """Run a non-control tool. Raises ToolRefused if policy forbids it."""
    if name not in context.allowed or name not in _SPECS:
        raise ToolRefused(f"{context.role} is not allowed to use '{name}'")
    if name == "workspace_list":
        root = context.workspace.resolve()
        files = sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file())
        return json.dumps(files, ensure_ascii=False)
    if name == "workspace_read":
        path = _resolve(context.workspace, arguments.get("path"))
        if not path.is_file():
            return f"No such file: {arguments.get('path')}"
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ToolRefused("file is too large to read")
        return path.read_text(encoding="utf-8", errors="replace")
    if name == "workspace_write":
        path = _resolve(context.workspace, arguments.get("path"))
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ToolRefused("content must be a string")
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise ToolRefused("content is too large to write")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        return f"Wrote {arguments.get('path')}"
    if name == "memory_recall":
        hits = context.memory.recall(context.role, str(arguments.get("query", "")))
        return untrusted("memory", json.dumps(hits, ensure_ascii=False))
    if name == "memory_write":
        try:
            context.memory.write(context.role, str(arguments.get("text", "")))
        except PermissionError as error:
            raise ToolRefused(str(error)) from error
        return "Saved."
    if name == "skill_search":
        found = context.skills.search(context.role, str(arguments.get("query", "")))
        return untrusted("skill-search", json.dumps(found, ensure_ascii=False))
    raise ToolRefused(f"unknown tool '{name}'")
