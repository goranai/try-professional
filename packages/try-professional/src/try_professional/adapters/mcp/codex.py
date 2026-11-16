"""Codex's AGENTS.md and project MCP config, sharing the company's other front desks.

Codex 0.163 accepts command and args under mcp_servers in project .codex/config.toml:
https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/core/config.schema.json (checked 2026-11-16).
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterator
from pathlib import Path

from ...org import Org
from .claude_code import SERVER, server_config
from .front_desk import FrontDeskError, front_desk_rules, managed_text, write_managed

CONFIG_START = "# trypro:mcp:start"
CONFIG_END = "# trypro:mcp:end"
CLIENT = "codex"

# Strings are single tokens so a comment or example inside a value cannot look like a server definition.
_TOML_TOKENS = re.compile(
    r'"""(?:\\[\s\S]|(?!""")[\s\S])*"""'
    r"|'''[\s\S]*?'''"
    r'|"(?:\\[^\r\n]|[^"\\\r\n])*"'
    r"|'[^'\r\n]*'"
    r"|#[^\n]*|[A-Za-z0-9_-]+|[^\s]|\n"
)


def _toml_statements(text: str) -> Iterator[list[str]]:
    statement: list[str] = []
    depth = 0
    for match in _TOML_TOKENS.finditer(text):
        token = match.group()
        if token.startswith("#"):
            continue
        if token == "\n" and depth == 0:
            if statement:
                yield statement
                statement = []
            continue
        statement.append(token)
        if token in ("[", "{"):
            depth += 1
        elif token in ("]", "}"):
            depth -= 1
    if statement:
        yield statement


def _has_unmanaged_server(text: str) -> bool:
    """QA found spaced headers and inline tables bypassed the old regex. Scan key paths without a Python 3.11 parser."""
    table: tuple[str, ...] = ()
    reserved = ("mcp_servers", SERVER)
    for statement in _toml_statements(text):
        header = statement[0] == "["
        if header:
            keys = statement[2:-2] if statement[:2] == ["[", "["] else statement[1:-1]
        elif "=" in statement:
            keys = statement[: statement.index("=")]
        else:
            continue
        if not keys or len(keys) % 2 == 0 or any(key != "." for key in keys[1::2]):
            continue
        path = tuple(
            key[1:-1] if key.startswith("'") else ast.literal_eval(key) if key.startswith('"') else key
            for key in keys[::2]
        )
        if header:
            table = path
        else:
            path = table + path
        if path[:2] == reserved or (not header and reserved[: len(path)] == path):
            # A value assigned to mcp_servers (including an inline table) cannot be extended with our table.
            return True
    return False


def codex_config(root: Path) -> str:
    """Leave an unmarked personal server alone rather than taking it over or writing duplicate TOML tables."""
    config = root / ".codex" / "config.toml"
    text = config.read_text(encoding="utf-8") if config.is_file() else ""
    personal = managed_text(text, "", start=CONFIG_START, end=CONFIG_END, append=True)
    # An unmarked definition belongs to the person, even when it is outside an existing managed block.
    if _has_unmanaged_server(personal):
        raise FrontDeskError(".codex/config.toml already has an unmanaged mcp_servers.trypro table. Rename it first.")
    server = server_config(root)
    args = [*server["args"], "--client", CLIENT]
    # TOML accepts Unicode directly, but rejects JSON's surrogate-pair escapes for non-BMP folder names.
    command = json.dumps(server["command"], ensure_ascii=False)
    block = f"[mcp_servers.{SERVER}]\ncommand = {command}\nargs = {json.dumps(args, ensure_ascii=False)}"
    # Appending a table keeps the person's existing top-level keys in their original TOML scope.
    return managed_text(text, block, start=CONFIG_START, end=CONFIG_END, append=True)


def write_codex_files(root: Path, org: Org) -> list[Path]:
    """Only our rules and server are managed. Other project settings and MCP servers keep their exact text."""
    text = codex_config(root)
    folder = root / ".codex"
    folder.mkdir(parents=True, exist_ok=True)
    config = folder / "config.toml"
    config.write_text(text, encoding="utf-8", newline="\n")
    return [write_managed(root / "AGENTS.md", front_desk_rules(org, CLIENT)), config]
