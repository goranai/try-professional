"""Connects the pure pipeline to persistence, audit, memory, and skills.

Rejected events are audited as violations and re-raised. Actions that need no
model (resolving context) are handled here, so adapters see only what they must do.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..org import Org
from .audit import AuditLog, VerifyResult, verify_log
from .ledger import Ledger
from .pipeline import (
    Action,
    ContextResolved,
    DepartmentActivated,
    Finish,
    IllegalEvent,
    Purpose,
    ResolveContext,
    RunStarted,
    RunState,
    Submission,
    advance,
    initial_state,
    open_actions,
)
from .ports import FeedbackPort, MemoryPort, NullMemory, NullSkills, ProposalPort, SkillPort

Clock = Callable[[], str]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Orchestrator:
    def __init__(
        self,
        org: Org,
        home: Path,
        *,
        memory: MemoryPort | None = None,
        skills: SkillPort | None = None,
        proposals: ProposalPort | None = None,
        feedback: FeedbackPort | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self.org = org
        self.home = home
        self.memory: MemoryPort = memory or NullMemory()
        self.skills: SkillPort = skills or NullSkills()
        self.proposals = proposals
        self.feedback = feedback
        self.clock = clock
        self.ledger = Ledger(home / "ledger.sqlite")
        self._lock = threading.RLock()
        self._logs: dict[str, AuditLog] = {}

    def audit_path(self, run_id: str) -> Path:
        return self.home / "runs" / run_id / "audit.jsonl"

    def workspace(self, run_id: str) -> Path:
        path = self.home / "runs" / run_id / "workspace"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _log(self, run_id: str) -> AuditLog:
        if run_id not in self._logs:
            self._logs[run_id] = AuditLog(self.audit_path(run_id))
        return self._logs[run_id]

    def start(self, goal: str, run_id: str | None = None) -> tuple[str, list[Action]]:
        with self._lock:
            run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
            state = initial_state(run_id, goal)
            self._log(run_id).append(self.clock(), "run_started", {"run_id": run_id, "goal": goal})
            return run_id, self._apply(state, RunStarted())

    def handle(self, run_id: str, event: Any) -> list[Action]:
        """Apply an event from an adapter and return what must happen next."""
        with self._lock:
            state = self.ledger.load(run_id)
            return self._apply(state, event)

    def _apply(self, state: RunState, event: Any) -> list[Action]:
        pending_events: list[Any] = [event]
        outward: list[Action] = []
        while pending_events:
            current = pending_events.pop(0)
            log = self._log(state.run_id)
            before = state.phase
            previous = state
            try:
                state, actions = advance(state, current, self.org)
            except IllegalEvent as error:
                state.counters.violations += 1
                log.append(
                    self.clock(),
                    "violation",
                    {"code": error.code, "message": str(error), "event": current.model_dump(mode="json")},
                )
                self.ledger.save(state, log.head, self.clock())
                raise
            log.append(self.clock(), "event", current.model_dump(mode="json"))
            self._log_rejection(state, current, log)
            self._note_qa_verdict(previous, state, current, log)
            if state.phase != before:
                log.append(self.clock(), "phase", {"from": before.value, "to": state.phase.value})
            for action in actions:
                log.append(self.clock(), "action", action.model_dump(mode="json"))
                if isinstance(action, ResolveContext):
                    pending_events.append(self._resolve_context(state, action))
                elif isinstance(action, Finish) and action.status == "done":
                    self._store_lessons(state)
                    self._safely(state, "run_finished", state.run_id, sorted(state.usage_by_role))
                    outward.append(action)
                elif not isinstance(action, DepartmentActivated):
                    outward.append(action)
            self.ledger.save(state, log.head, self.clock())
        return outward

    def _safely(self, state: RunState, method: str, *args: Any, **kwargs: Any) -> None:
        """Feedback improves later runs, and a failure in it must never stop this one."""
        if self.feedback is None:
            return
        try:
            getattr(self.feedback, method)(*args, **kwargs)
        except Exception as error:
            self._log(state.run_id).append(self.clock(), "feedback_error", {"error": type(error).__name__})

    def _note_qa_verdict(self, previous: RunState, state: RunState, event: Any, log: AuditLog) -> None:
        """A QA verdict that was accepted tells us how the task's skills did."""
        if not isinstance(event, Submission):
            return
        invocation = previous.invocations.get(event.invocation_id)
        if invocation is None or invocation.purpose != Purpose.REVIEW or invocation.gate != "qa":
            return
        if event.invocation_id in state.invocations or invocation.task_id is None:
            return
        passed = event.payload.get("decision") == "approve"
        self._safely(state, "qa_outcome", state.run_id, invocation.task_id, passed=passed)

    def skill_used(self, run_id: str, task_id: str | None, role: str, skill_id: str) -> None:
        """A role fetched a skill for a task. Recorded for the audit trail and for re-ranking."""
        with self._lock:
            state = self.ledger.load(run_id)
            log = self._log(run_id)
            log.append(self.clock(), "skill_used", {"role": role, "skill_id": skill_id, "task_id": task_id})
            self.ledger.save(state, log.head, self.clock())
            self._safely(state, "skill_used", run_id, task_id or "", role, skill_id)

    def _log_rejection(self, state: RunState, event: Any, log: AuditLog) -> None:
        """A submission that left its invocation open was rejected. Record why."""
        invocation_id = getattr(event, "invocation_id", None)
        invocation = state.invocations.get(invocation_id or "")
        if isinstance(event, Submission) and invocation is not None and invocation.feedback:
            log.append(
                self.clock(),
                "rejected",
                {"invocation_id": invocation.id, "role": invocation.role, "reason": invocation.feedback[-1]},
            )

    def _resolve_context(self, state: RunState, action: ResolveContext) -> ContextResolved:
        log = self._log(state.run_id)
        # Memory helps a task but isn't required for one, so a failure here is logged and survived.
        memory_context: list[str] = []
        candidates: list[dict[str, Any]] = []
        try:
            memory_context = self.memory.recall(action.role, action.query)
            candidates = self.skills.search(action.role, action.query)
        except Exception as error:
            log.append(self.clock(), "memory_error", {"role": action.role, "error": type(error).__name__})
        log.append(
            self.clock(), "memory_recall", {"role": action.role, "query": action.query, "hits": len(memory_context)}
        )
        log.append(self.clock(), "skill_search", {"role": action.role, "query": action.query, "hits": len(candidates)})
        self._safely(state, "note_search", state.run_id, action.task_id, action.role, action.query, candidates)
        return ContextResolved(
            task_id=action.task_id,
            query=action.query,
            memory_context=memory_context,
            skill_candidates=candidates,
        )

    def _store_lessons(self, state: RunState) -> None:
        """Retrospective: lessons and procedures become company memory, linked to their source."""
        artifact = state.artifacts.get(state.retrospective_artifact_id or "")
        if artifact is None:
            return
        log = self._log(state.run_id)
        items = [*artifact.payload.get("lessons", []), *artifact.payload.get("procedures", [])]
        for text in items:
            try:
                self.memory.promote(self.org.root_role, text, artifact.artifact_id)
            except Exception as error:
                log.append(self.clock(), "memory_error", {"role": self.org.root_role, "error": type(error).__name__})
                return
        log.append(self.clock(), "lessons_stored", {"count": len(items), "source": artifact.artifact_id})
        if self.proposals is None:
            return
        for text in artifact.payload.get("skill_proposals", []):
            try:
                name = self.proposals.propose(text, source=artifact.artifact_id)
                log.append(self.clock(), "skill_proposed", {"name": name, "source": artifact.artifact_id})
            except Exception as error:
                log.append(self.clock(), "proposal_error", {"error": type(error).__name__})

    def record(self, run_id: str, kind: str, data: dict[str, Any]) -> None:
        """Add a non-state record, such as a refused tool call."""
        with self._lock:
            state = self.ledger.load(run_id)
            log = self._log(run_id)
            if kind == "violation":
                state.counters.violations += 1
            elif kind == "invalid_output":
                state.counters.invalid_outputs += 1
            log.append(self.clock(), kind, data)
            self.ledger.save(state, log.head, self.clock())

    def state(self, run_id: str) -> RunState:
        with self._lock:
            return self.ledger.load(run_id)

    def open_actions(self, run_id: str) -> list[Action]:
        """What is waiting right now. This is how a run resumes after a crash."""
        with self._lock:
            state = self.ledger.load(run_id)
            actions = open_actions(state)
            events = [a for a in actions if isinstance(a, ResolveContext)]
            if not events:
                return actions
            outward: list[Action] = []
            for action in events:
                outward += self._apply(state, self._resolve_context(state, action))
                state = self.ledger.load(run_id)
            return open_actions(state)

    def verify_audit(self, run_id: str) -> VerifyResult:
        return verify_log(self.audit_path(run_id), self.ledger.audit_head(run_id))

    def close(self) -> None:
        self.ledger.close()
