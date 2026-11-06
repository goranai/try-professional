"""The adapter interface. Copy adapters/template/ (Phase 5) to add a runtime.

Runtimes differ in who owns the agent loop, so the interface states it explicitly:

    push:  the adapter calls the model and returns the result (API runner)
    pull:  a client asks for the next action, does it, and submits (MCP)
    queue: the adapter enqueues work, and a worker picks it up later (Hermes)
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from ..core.pipeline import InvokeRole
from ..org import Org

Mode = Literal["push", "pull", "queue"]


@dataclass(frozen=True)
class Capabilities:
    """What the runtime can guarantee. The audit log reflects these honestly."""

    attests_identity: bool
    enforces_tools: bool
    max_parallel: int | None = None


@dataclass
class ProvisionReport:
    created: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class VerifyReport:
    ok: bool
    checks: list[tuple[str, bool, str]] = field(default_factory=list)


@dataclass
class Delivery:
    """Events produced now (push), or nothing if results will arrive later (pull, queue)."""

    events: list[Any] = field(default_factory=list)


@runtime_checkable
class RuntimeAdapter(Protocol):
    name: str
    mode: Mode

    def capabilities(self) -> Capabilities: ...

    def provision(self, org: Org, home: Path) -> ProvisionReport: ...

    def verify(self) -> VerifyReport: ...

    def deliver(self, action: InvokeRole, run_id: str) -> Delivery: ...

    def collect(self, run_id: str) -> Iterable[Any]: ...
