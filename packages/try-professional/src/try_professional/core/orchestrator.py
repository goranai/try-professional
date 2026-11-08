"""Connects the pure pipeline to persistence, audit, memory, and skills.

Rejected events are audited as violations and re-raised. Actions that need no
model (resolving context) are handled here, so adapters see only what they must do.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .. import playbooks
from ..org import Org
from .audit import AuditLog, VerifyResult, archive_log, verify_log
from .brief import Briefing, build_brief
from .company import CompanyStore
from .deliverables import DRAFT_FOLDER, export_deliverables, export_drafts, read_sources
from .ledger import Ledger
from .lock import StateLock
from .pipeline import (
    Action,
    ContextResolved,
    DepartmentActivated,
    Finish,
    IllegalEvent,
    Purpose,
    ResolveContext,
    RunCancelled,
    RunPhase,
    RunStarted,
    RunState,
    Submission,
    advance,
    initial_state,
    open_actions,
)
from .pipeline.state import Invocation
from .ports import FeedbackPort, MemoryPort, NullMemory, NullSkills, ProposalPort, SearchPort, SkillPort

Clock = Callable[[], str]
MAX_INPUT_BYTES = 2_000_000
MAX_EARLIER_RUNS = 5
EARLIER_WORK = "earlier"
PENDING_LESSONS_SHOWN = 3
TEAM_SKILLS_SHOWN = 3


_LOOKS_LIKE_A_FILE = re.compile(r"[\w./ -]+\.[A-Za-z0-9]{1,8}")


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
        search: SearchPort | None = None,
        inputs_dir: Path | None = None,
        deliverables_dir: Path | None = None,
        clock: Clock = utc_now,
        check_claimed_files: bool = True,
    ) -> None:
        self.check_claimed_files = check_claimed_files
        self.org = org
        self.home = home
        self.memory: MemoryPort = memory or NullMemory()
        self.skills: SkillPort = skills or NullSkills()
        self.proposals = proposals
        self.feedback = feedback
        self.search = search
        self.inputs_dir = inputs_dir
        self.deliverables_dir = deliverables_dir
        self.clock = clock
        self.ledger = Ledger(home / "ledger.sqlite")
        self.company = CompanyStore(home / "company.sqlite")
        # Several MCP servers can serve one company, one per role, so this also holds across processes.
        self._lock = StateLock(home)
        self._logs: dict[str, AuditLog] = {}

    def audit_path(self, run_id: str) -> Path:
        return self.home / "runs" / run_id / "audit.jsonl"

    def workspace(self, run_id: str) -> Path:
        path = self.home / "runs" / run_id / "workspace"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def sources_path(self, run_id: str) -> Path:
        return self.home / "runs" / run_id / "sources.jsonl"

    def deliverables_path(self, run_id: str) -> Path | None:
        return self.deliverables_dir / run_id if self.deliverables_dir else None

    def note_source(self, run_id: str, source: str, day: str = "") -> None:
        """What this run really retrieved, so the export can tell a real citation from an invented one."""
        if not source:
            return
        path = self.sources_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"source": source, "date": day}, ensure_ascii=False) + "\n")

    def _earlier_runs(self, run_id: str) -> list[Path]:
        """The newest finished work. Only a finished run has deliverables, so this has passed its gates."""
        if self.deliverables_dir is None or not self.deliverables_dir.is_dir():
            return []
        runs = [p for p in self.deliverables_dir.iterdir() if p.is_dir() and p.name != run_id]
        return sorted(runs, key=lambda p: p.stat().st_mtime, reverse=True)[:MAX_EARLIER_RUNS]

    def _copy_inputs(self, run_id: str) -> None:
        """Give this run its own copy, so what a run read stays on record if the folder changes later.

        A permanent company has to know what it already did. So its finished work comes too, whatever a model
        remembered to write down, and read-only, like everything else a person provided.
        """
        target = self.workspace(run_id) / "inputs"
        sources = [(run, Path(EARLIER_WORK) / run.name) for run in self._earlier_runs(run_id)]
        if self.inputs_dir is not None and self.inputs_dir.is_dir():
            sources.insert(0, (self.inputs_dir, Path()))
        copied = []
        for folder, prefix in sources:
            for path in sorted(folder.rglob("*")):
                if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_INPUT_BYTES:
                    continue
                relative = path.relative_to(folder)
                if any(part.startswith(".") for part in relative.parts):
                    continue
                destination = target / prefix / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
                name = f"inputs/{(prefix / relative).as_posix()}"
                self.note_source(run_id, name)
                copied.append({"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        if copied:
            self._log(run_id).append(self.clock(), "inputs_copied", {"files": copied})

    def _export(self, state: RunState) -> None:
        """The run already succeeded, so a failure here is reported and doesn't undo it."""
        target = self.deliverables_path(state.run_id)
        if target is None:
            return
        log = self._log(state.run_id)
        try:
            index = export_deliverables(
                state, self.workspace(state.run_id), target, read_sources(self.sources_path(state.run_id))
            )
            log.append(self.clock(), "deliverables_exported", {"index": index.name})
        except OSError as error:
            log.append(self.clock(), "deliverables_error", {"error": type(error).__name__})

    def drafts_path(self, run_id: str) -> Path | None:
        target = self.deliverables_path(run_id)
        return target / DRAFT_FOLDER if target else None

    def _export_drafts(self, state: RunState) -> None:
        """A stopped run still hands over what it made, marked as not approved."""
        target = self.drafts_path(state.run_id)
        if target is None:
            return
        log = self._log(state.run_id)
        try:
            index = export_drafts(
                state, self.workspace(state.run_id), target, read_sources(self.sources_path(state.run_id))
            )
            log.append(self.clock(), "drafts_exported", {"index": f"{DRAFT_FOLDER}/{index.name}"})
        except OSError as error:
            log.append(self.clock(), "drafts_error", {"error": type(error).__name__})

    def _log(self, run_id: str) -> AuditLog:
        log = self._logs.get(run_id)
        if log is None or log.changed_elsewhere():
            log = self._logs[run_id] = AuditLog(self.audit_path(run_id))
        return log

    def start(self, goal: str, run_id: str | None = None) -> tuple[str, list[Action]]:
        with self._lock:
            run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
            state = initial_state(run_id, goal)
            log = self._log(run_id)
            log.append(self.clock(), "run_started", {"run_id": run_id, "goal": goal})
            log.append(self.clock(), "playbooks", playbooks.versions())
            self._copy_inputs(run_id)
            return run_id, self._apply(state, RunStarted())

    def handle(self, run_id: str, event: Any) -> list[Action]:
        """Apply an event from an adapter and return what must happen next."""
        with self._lock:
            state = self.ledger.load(run_id)
            if isinstance(event, Submission) and self.check_claimed_files:
                event = event.model_copy(update={"missing_files": self._claimed_but_missing(state, event)})
            return self._apply(state, event)

    def _claimed_but_missing(self, state: RunState, event: Submission) -> list[str]:
        """Files a role says it produced, and didn't. A model can claim anything, and the workspace can't."""
        invocation = state.invocations.get(event.invocation_id)
        if invocation is None or invocation.purpose != Purpose.EXECUTE:
            return []
        claimed: list[str] = []
        if invocation.output_type == "CodeChange":
            files = event.payload.get("files")
            claimed = [str(f.get("path", "")) for f in files if isinstance(f, dict)] if isinstance(files, list) else []
        elif invocation.output_type == "TaskReport":
            outputs = event.payload.get("outputs")
            named = [str(item) for item in outputs] if isinstance(outputs, list) else []
            claimed = [item for item in named if _LOOKS_LIKE_A_FILE.fullmatch(item)]
        workspace = self.workspace(state.run_id).resolve()
        missing = []
        for name in claimed:
            target = (workspace / name).resolve()
            if not target.is_relative_to(workspace) or not target.is_file():
                missing.append(name)
        return missing

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
            self._note_lesson_decisions(previous, state, current, log)
            if state.phase != before:
                log.append(self.clock(), "phase", {"from": before.value, "to": state.phase.value})
                if state.phase in (RunPhase.BLOCKED, RunPhase.FAILED):
                    self._export_drafts(state)
            for action in actions:
                log.append(self.clock(), "action", action.model_dump(mode="json"))
                if isinstance(action, ResolveContext):
                    pending_events.append(self._resolve_context(state, action))
                elif isinstance(action, Finish) and action.status == "done":
                    self._propose_lessons(state)
                    self._export(state)
                    self._safely(state, "run_finished", state.run_id, sorted(state.usage_by_role))
                    outward.append(action)
                elif not isinstance(action, DepartmentActivated):
                    outward.append(action)
            self.ledger.save(state, log.head, self.clock())
            self._sync_company(state, log)
        return outward

    def _sync_company(self, state: RunState, log: AuditLog) -> None:
        """Work state follows the run. Lessons in a role's own notes go where they belong."""
        for entry in self.company.sync(state, self.org, self.clock()):
            try:
                if entry.kind == "lesson":
                    self.memory.write(entry.role, entry.text)
                elif entry.kind == "team_lesson":
                    self.company.propose_memory(
                        "department",
                        entry.role.split("/", 1)[0],
                        entry.text,
                        source_run=state.run_id,
                        source=entry.key,
                        proposed_by=entry.role,
                        at=self.clock(),
                    )
            except Exception as error:
                log.append(self.clock(), "memory_error", {"role": entry.role, "error": type(error).__name__})

    def briefing(self, run_id: str, invocation: Invocation) -> Briefing:
        """What an agent is told when it wakes up. It costs no model call, and failing never stops a run."""
        with self._lock:
            state = self.ledger.load(run_id)
            role = invocation.role
            personal: list[str] = []
            # A task's memory is recalled with its context, so only other steps need it here.
            if invocation.purpose not in (Purpose.EXECUTE, Purpose.REVIEW) and role not in state.usage_by_role:
                try:
                    personal = self.memory.recall_personal(role, state.goal)
                except Exception as error:
                    self._log(run_id).append(
                        self.clock(), "memory_error", {"role": role, "error": type(error).__name__}
                    )
            # One stand-up per agent per command. Later steps in the same command have their own context.
            brief = None
            if role not in state.usage_by_role:
                brief = build_brief(
                    self.org, self.company, role, invocation.purpose, size=state.size, personal=personal
                )
            lessons: list[dict[str, Any]] = []
            team: dict[str, dict[str, Any]] = {}
            if invocation.purpose == Purpose.DISPATCH:
                department = role.split("/", 1)[0]
                pending = self.company.pending_memory("department", department, limit=PENDING_LESSONS_SHOWN)
                lessons = [{"number": p.id, "from": p.proposed_by, "lesson": p.text} for p in pending]
                members = [f"{department}/{r}" for r in self.org.departments[department].roles]
                counts = self.company.open_task_counts(members)
                for member in members:
                    skills = [str(item.get("name") or item.get("skill_id", "")) for item in self.skills.loadout(member)]
                    team[member] = {"open_tasks": counts[member], "skills": skills[:TEAM_SKILLS_SHOWN]}
        return Briefing(brief=brief, pending_lessons=lessons, team=team)

    def cancel(self, run_id: str, reason: str = "Cancelled by a person") -> None:
        self.handle(run_id, RunCancelled(reason=reason))

    def _note_lesson_decisions(self, previous: RunState, state: RunState, event: Any, log: AuditLog) -> None:
        """A head decided its team's lessons while dispatching. Only that department's pending ones count."""
        if not isinstance(event, Submission):
            return
        invocation = previous.invocations.get(event.invocation_id)
        if invocation is None or invocation.purpose != Purpose.DISPATCH or event.invocation_id in state.invocations:
            return
        department = invocation.role.split("/", 1)[0]
        own = {p.id for p in self.company.pending_memory("department", department)}
        for key, accepted in (("adopt_lessons", True), ("drop_lessons", False)):
            for number in event.payload.get(key) or []:
                if number not in own:
                    continue
                decided = self.company.decide_memory(number, accepted, by=invocation.role, at=self.clock())
                if decided is None:
                    continue
                log.append(self.clock(), "lesson_decided", {"number": number, "adopted": accepted})
                if accepted:
                    try:
                        self.memory.write_department(invocation.role, decided.text)
                    except Exception as error:
                        log.append(
                            self.clock(), "memory_error", {"role": invocation.role, "error": type(error).__name__}
                        )

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

    def _propose_lessons(self, state: RunState) -> None:
        """Retrospective: the root role proposes company memory, and a person decides. Nothing is written here."""
        artifact = state.artifacts.get(state.retrospective_artifact_id or "")
        if artifact is None:
            return
        log = self._log(state.run_id)
        items = [*artifact.payload.get("lessons", []), *artifact.payload.get("procedures", [])]
        for text in items:
            self.company.propose_memory(
                "company",
                "company",
                text,
                source_run=state.run_id,
                source=artifact.artifact_id,
                proposed_by=self.org.root_role,
                at=self.clock(),
            )
        log.append(self.clock(), "lessons_proposed", {"count": len(items), "source": artifact.artifact_id})
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

    def compact(self, older_than_days: int = 90) -> dict[str, int]:
        """Keep context and storage from growing without limit. Uses no model, and loses no raw record."""
        with self._lock:
            cutoff = (datetime.fromisoformat(self.clock()) - timedelta(days=older_than_days)).isoformat()
            removed = self.company.compact(cutoff)
            archived = 0
            for run_id, updated in self.ledger.list_runs():
                if updated >= cutoff or self.ledger.is_archived(run_id):
                    continue
                if self.ledger.load(run_id).phase.value not in ("done", "failed"):
                    continue
                self._logs.pop(run_id, None)
                archive_log(self.audit_path(run_id))
                self.ledger.archive(run_id)
                archived += 1
        return {"log_entries_summarized": removed, "runs_archived": archived}

    def rebuild_company(self) -> None:
        """Work state is derived, so it can always be rebuilt from the ledger."""
        with self._lock:
            states = [self.ledger.load(run_id) for run_id, _ in reversed(self.ledger.list_runs())]
            self.company.rebuild(states, self.org, self.clock())

    def verify_audit(self, run_id: str) -> VerifyResult:
        return verify_log(self.audit_path(run_id), self.ledger.audit_head(run_id))

    def close(self) -> None:
        self.ledger.close()
        self.company.close()
