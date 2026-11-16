"""Usage printed by agent CLIs, with their cache conventions preserved.

The 0.7.4 brief reported empty step counts and tool estimates presented as 36M
model tokens. Only structured CLI usage establishes the requested split.
Evidence and captured formats are in tests/fixtures/cli_usage/README.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Literal


@dataclass(frozen=True)
class CliUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    raw_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    raw_input_tokens_includes_cache: bool | None = None
    status: Literal["exact", "partial", "unavailable", "unverified"] = "unavailable"
    source: str = ""
    note: str = "No supported structured CLI usage was printed."
    session_id: str | None = None
    reported: tuple[dict[str, Any], ...] = ()

    @property
    def total(self) -> int | None:
        return self.input_tokens + self.output_tokens if self.status == "exact" else None


def _objects(output: str) -> list[dict[str, Any]]:
    """Accept JSONL and pretty JSON without mistaking nested usage for a result."""
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    offset = 0
    while offset < len(output):
        start = offset
        while start < len(output) and output[start].isspace():
            start += 1
        try:
            value, end = decoder.raw_decode(output, start)
        except ValueError:
            newline = output.find("\n", start)
            if newline < 0:
                break
            offset = newline + 1
            continue
        if isinstance(value, dict):
            objects.append(value)
        elif isinstance(value, list):
            objects.extend(item for item in value if isinstance(item, dict))
        offset = end
    return objects


def _count(usage: dict[str, Any], key: str, *, default: int | None = None) -> int:
    value = usage.get(key, default)
    if type(value) is not int or value < 0:
        raise ValueError(f"{key} is missing or is not a non-negative integer.")
    return value


def _claude_model_usage(item: dict[str, Any], counts: tuple[int, int, int, int]) -> None:
    """The captured 403 has default counters and no model breakdown; it is not a measurement."""
    models = item.get("modelUsage")
    if not isinstance(models, dict) or not models:
        raise ValueError("The Claude result has no model usage provenance.")
    keys = ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens", "outputTokens")
    if not all(isinstance(model, dict) for model in models.values()):
        raise ValueError("The Claude model usage breakdown is malformed.")
    totals = tuple(sum(_count(model, key) for model in models.values()) for key in keys)
    if totals != counts:
        raise ValueError("The Claude model breakdown contradicts its usage counters.")


def cli_usage(runtime: str, output: str, *, exit_code: int | None = 0) -> CliUsage:
    """The last cumulative snapshot, never a sum of repeated result events."""
    found = CliUsage(source=f"{runtime}: unavailable")
    known: CliUsage | None = None
    reported: list[dict[str, Any]] = []
    session: str | None = None
    for item in _objects(output):
        if runtime == "codex" and item.get("type") == "thread.started":
            session = item.get("thread_id") if isinstance(item.get("thread_id"), str) else None
        if runtime == "codex" and item.get("type") == "turn.failed":
            reported.append({"type": "turn.failed"})
            found = CliUsage(source="codex: turn.failed", note="The final turn failed; complete usage is unavailable.")
            continue
        if runtime == "codex":
            supported = item.get("type") == "turn.completed"
            source = "codex: turn.completed usage (rust-v0.169.0)"
            includes_cache, cached_key = True, "cached_input_tokens"
            created_key = "cache_write_input_tokens"
        elif runtime == "antigravity":
            supported = isinstance(item.get("status"), str) and "conversation_id" in item
            source = "antigravity: conversation usage (agy 1.5.6)"
            includes_cache, cached_key = False, "cache_read_tokens"
            created_key = ""
            session = item.get("conversation_id") if isinstance(item.get("conversation_id"), str) else None
        elif runtime == "claude-code":
            supported = item.get("type") == "result"
            source = "claude-code: result usage (2.1.325)"
            includes_cache, cached_key = False, "cache_read_input_tokens"
            created_key = "cache_creation_input_tokens"
            session = item.get("session_id") if isinstance(item.get("session_id"), str) else None
        else:
            return found
        if not supported:
            continue
        reported.append(
            {
                key: item[key]
                for key in ("type", "status", "is_error", "subtype", "terminal_reason", "usage", "modelUsage")
                if key in item
            }
        )
        usage = item.get("usage")
        if not isinstance(usage, dict):
            found = CliUsage(source=source, note="The CLI result has no usage object.", session_id=session)
            continue
        try:
            raw = _count(usage, "input_tokens")
            cached = _count(usage, cached_key)
            output_count = _count(usage, "output_tokens")
            if (
                runtime == "antigravity"
                and "total_tokens" in usage
                and (_count(usage, "total_tokens") != raw + output_count)
            ):
                raise ValueError("The CLI total contradicts its input/output counters.")
            created = _count(usage, created_key, default=0 if runtime == "codex" else None) if created_key else 0
            if includes_cache and cached + created > raw:
                raise ValueError("Cache counts exceed the inclusive input count.")
            # Codex's non_cached_input subtracts cache reads from input. Antigravity's
            # captured continuation has 11,566 new cache reads but only 2,315 new
            # raw input tokens. Claude's local provider probe preserves separate reads/writes.
            inputs = raw if includes_cache else raw + cached + created
            if runtime == "claude-code":
                _claude_model_usage(item, (raw, cached, created, output_count))
            elif inputs + output_count == 0:
                # Codex rust-v0.169.0 usage_from_last_total emits Usage::default()
                # without provider usage. A bare zero snapshot cannot distinguish it.
                raise ValueError("Zero CLI counters alone do not establish a provider usage measurement.")
            succeeded = True
            if runtime == "claude-code":
                succeeded = (
                    item.get("is_error") is False
                    and item.get("subtype", "success") == "success"
                    and item.get("terminal_reason", "completed") == "completed"
                )
            elif runtime == "antigravity":
                succeeded = item.get("status") == "SUCCESS"
            if not succeeded and inputs + output_count == 0:
                raise ValueError("A failed result with zero counters does not establish provider usage.")
            found = CliUsage(
                input_tokens=inputs,
                cached_input_tokens=cached,
                output_tokens=output_count,
                raw_input_tokens=raw,
                cache_creation_input_tokens=created,
                raw_input_tokens_includes_cache=includes_cache,
                status="exact" if succeeded else "partial",
                source=source,
                note="Counts reported by the CLI; normalized input includes cache reads and writes once."
                if succeeded
                else "The CLI reported model counts, but its failed result leaves complete usage unavailable.",
                session_id=session,
            )
            known = found
        except ValueError as error:
            found = CliUsage(status="unverified", source=source, note=str(error), session_id=session)
    if found.status not in ("exact", "partial") and known is not None:
        found = replace(known, status="partial", note=f"Earlier verified counts retained; final usage: {found.note}")
    if exit_code != 0 and found.status == "exact":
        found = replace(
            found, status="partial", note="Verified printed counts retained; the CLI did not finish successfully."
        )
    return replace(found, reported=tuple(reported))


class CliUsageTracker:
    """Resumed Antigravity output repeats the conversation's earlier usage."""

    def __init__(self) -> None:
        self.previous: dict[str, CliUsage] = {}

    def read(self, runtime: str, output: str, *, exit_code: int | None = 0) -> CliUsage:
        current = cli_usage(runtime, output, exit_code=exit_code)
        if current.status not in ("exact", "partial") or runtime != "antigravity" or current.session_id is None:
            return current
        before = self.previous.get(current.session_id)
        if before is None:
            self.previous[current.session_id] = current
            return current
        counts = ("input_tokens", "cached_input_tokens", "output_tokens", "raw_input_tokens")
        if any(getattr(current, key) < getattr(before, key) for key in counts):
            return CliUsage(
                status="unverified",
                source=current.source,
                session_id=current.session_id,
                note="Conversation usage decreased; its delta cannot be verified.",
                reported=current.reported,
            )
        self.previous[current.session_id] = current
        return replace(
            current,
            input_tokens=current.input_tokens - before.input_tokens,
            cached_input_tokens=current.cached_input_tokens - before.cached_input_tokens,
            output_tokens=current.output_tokens - before.output_tokens,
            raw_input_tokens=current.raw_input_tokens - before.raw_input_tokens,
            note="Delta from the previous CLI snapshot of this conversation; cache reads counted once. " + current.note,
        )
