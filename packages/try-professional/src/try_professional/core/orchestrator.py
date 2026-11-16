"""Connects the pure pipeline to persistence, audit, memory, and skills.

Rejected events are audited as violations and re-raised. Actions that need no
model (resolving context) are handled here, so adapters see only what they must do.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import shutil
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .. import playbooks
from ..org import Org
from . import org_growth, scratch
from .audit import AuditLog, VerifyResult, archive_log, verify_log
from .brief import Briefing, build_brief
from .company import DROPPED_BY, CompanyStore
from .deliverables import (
    DRAFT_FOLDER,
    environments,
    export_deliverables,
    export_drafts,
    made_by_a_machine,
    read_sources,
)
from .ledger import Ledger
from .lock import StateLock
from .outside import RUN_ID, Held, Outcome, OutsideStore, describe_link, outcome_lesson, urls_in
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
    UsageRecorded,
    advance,
    initial_state,
    open_actions,
)
from .pipeline.base import TRUST
from .pipeline.state import Invocation
from .ports import FeedbackPort, MemoryPort, NullMemory, NullSkills, ProposalPort, SearchPort, SkillPort
from .scorecard import Scorecard, scorecard

Clock = Callable[[], str]
MAX_INPUT_BYTES = 2_000_000
MAX_EARLIER_RUNS = 5
EARLIER_WORK = "earlier"
PENDING_LESSONS_SHOWN = 3
TEAM_SKILLS_SHOWN = 3
# Who is told what happened to earlier work outside: whoever plans, assigns, does, or checks work.
OUTSIDE_PURPOSES = (Purpose.PLAN, Purpose.DISPATCH, Purpose.EXECUTE, Purpose.REVIEW)
Execute = Callable[[Path, str], str]
MAX_HELD_SHOWN = 1500


_LOOKS_LIKE_A_FILE = re.compile(r"[\w./ -]+\.[A-Za-z0-9]{1,8}")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# Never copied into a run: secrets, and what a machine makes for itself.
_SECRET_NAMES = re.compile(r"^\.env(\..+)?$|\.(pem|key|p12|pfx)$|^id_(rsa|ed25519|ecdsa)$", re.IGNORECASE)
_SAMPLES = re.compile(r"\.(example|sample|template|dist)$", re.IGNORECASE)


def _kept_input(relative: Path, *, in_repository: bool) -> bool:
    """A person's loose hidden files stay behind. A git repository comes whole, its history and its own dotfiles (CI,
    ignores) too: a role asked to work in it can't without them. Secrets and caches never come."""
    parts = relative.parts
    if made_by_a_machine(relative):
        return False
    if _SECRET_NAMES.search(parts[-1]) and not _SAMPLES.search(parts[-1]):
        return False
    return in_repository or not any(part.startswith(".") for part in parts)


def _with_where_it_is(workspace: Path, name: str) -> str:
    """A file named by its path in the repository rather than in the workspace: say where it is. A role on Gemini was
    refused twice for this, and then read the engine's code to find out why."""
    wanted = name.replace("\\", "/").strip("/")
    if not wanted or ".." in wanted.split("/"):
        return name
    for found in workspace.rglob(Path(wanted).name):
        relative = found.relative_to(workspace).as_posix()
        if found.is_file() and relative.endswith("/" + wanted):
            return f"{name} (it's at {relative}: name each file by its path in the workspace)"
    return name


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
        keep_run_scratch: bool = False,
    ) -> None:
        self.check_claimed_files = check_claimed_files
        self.keep_run_scratch = keep_run_scratch
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
        self.company = CompanyStore(home / "company.sqlite", embedder=getattr(self.memory, "embedder", None))
        self.outside = OutsideStore(home / "outside.sqlite")
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

    def scratch(self, run_id: str) -> Path:
        """The same path across role servers; reading it never recreates a finished run's storage."""
        path = scratch.scratch_path(self.home, run_id, self.ledger.scratch_path(run_id))
        if not path.exists():
            # Runs saved before this feature need storage when their next role resumes them.
            with self._lock:
                if (
                    not path.exists()
                    and self._has_run(run_id)
                    and self.ledger.load(run_id).phase not in (RunPhase.DONE, RunPhase.FAILED)
                ):
                    self._allocate_scratch(run_id)
        return path

    def _allocate_scratch(self, run_id: str) -> None:
        path = scratch.allocate(self.home, run_id, self.ledger.scratch_path(run_id))
        self.ledger.own_scratch(run_id, path)
        self._log(run_id).append(self.clock(), "scratch_allocated", {"path": str(path)})

    def _cleanup_scratch(self, state: RunState) -> None:
        data = {
            "status": "cancelled" if state.cancelled else state.phase.value,
        }
        try:
            allocated = self.ledger.scratch_path(state.run_id)
            data["path"] = str(scratch.scratch_path(self.home, state.run_id, allocated))
            if self.keep_run_scratch:
                data["outcome"] = "retained"
            else:
                data["outcome"] = "deleted" if scratch.cleanup(self.home, state.run_id, allocated) else "already_absent"
        except OSError as error:
            data.update(outcome="failed", error=type(error).__name__, reason=str(error))
            if "path" not in data:
                data["record"] = str(scratch.record_path(self.home, state.run_id))
        self._log(state.run_id).append(self.clock(), "scratch_cleanup", data)

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
            paths = sorted(scratch.paths_under(folder))
            files = [p for p in paths if p.is_file()]
            repositories = {p.parent.relative_to(folder) for p in paths if p.name == ".git" and p.is_dir()}
            found = environments(folder)
            for path in files:
                if not path.is_file() or path.is_symlink():
                    continue
                relative = path.relative_to(folder)
                if made_by_a_machine(relative, found):
                    continue
                repository = next((r for r in repositories if relative.is_relative_to(r)), None)
                inside = relative.relative_to(repository) if repository is not None else relative
                if not _kept_input(inside, in_repository=repository is not None):
                    continue
                history = repository is not None and inside.parts[0] == ".git"
                if path.stat().st_size > MAX_INPUT_BYTES and not history:
                    continue
                destination = target / prefix / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
                if history:
                    continue  # the repository's own history: needed to work in it, and no source of anything
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

    def _workers_path(self) -> Path:
        # Beside the company's database, not in it: an older trypro opening the same company reads on unchanged.
        return self.home / "workers.jsonl"

    def _record_workers(self, run_id: str, workers: Mapping[str, str]) -> None:
        path = self._workers_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            for role, worker in sorted(workers.items()):
                handle.write(json.dumps({"run_id": run_id, "role": role, "worker": worker}) + "\n")

    def _same_worker(self, workers: Mapping[str, str]) -> Callable[[str, str], bool]:
        """A hand-in counts toward a role's trust only if the same agent and model did it as do the role now. Trust
        earned on one model was once lent to another: a company moved to Gemini skipped the review of a role whose
        clean record was Claude's."""
        known: dict[tuple[str, str], str] = {}
        path = self._workers_path()
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                with contextlib.suppress(ValueError, KeyError, TypeError):
                    row = json.loads(line)
                    known[(row["run_id"], row["role"])] = row["worker"]
        return lambda run_id, role: role in workers and known.get((run_id, role)) == workers[role]

    def start(
        self, goal: str, run_id: str | None = None, workers: Mapping[str, str] | None = None
    ) -> tuple[str, list[Action]]:
        """`workers` names who does each role in this run, such as "antigravity:gemini-3.8-flash-high": then only
        hand-ins the same worker made count toward a role's trust."""
        with self._lock:
            run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
            state = initial_state(run_id, goal)
            log = self._log(run_id)
            log.append(self.clock(), "run_started", {"run_id": run_id, "goal": goal})
            log.append(self.clock(), "playbooks", playbooks.versions())
            try:
                counts = None
                if workers:
                    self._record_workers(run_id, workers)
                    counts = self._same_worker(workers)
                state.trust = self.company.trust(self.org.pipeline.trust_after, counts)
            except Exception as error:  # trust only saves work, so without it every hand-in is reviewed
                log.append(self.clock(), "trust_error", {"error": type(error).__name__})
            if state.trust:
                log.append(self.clock(), "trusted", {"roles": state.trust})
            try:
                self._allocate_scratch(run_id)
                self._copy_inputs(run_id)
                return run_id, self._apply(state, RunStarted())
            except Exception:
                state.phase = RunPhase.FAILED
                self._cleanup_scratch(state)
                raise

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
                missing.append(_with_where_it_is(workspace, name))
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
            if isinstance(current, Submission) and current.invocation_id not in state.invocations:
                invocation = previous.invocations.get(current.invocation_id)
                accepted = next(
                    (
                        a
                        for key, a in state.artifacts.items()
                        if key not in previous.artifacts and invocation and a.type == invocation.output_type
                    ),
                    None,
                )
                if accepted is None and invocation and invocation.purpose in (Purpose.EXECUTE, Purpose.INTEGRATE):
                    task = state.tasks.get(invocation.task_id or "")
                    if task and state.counters.invalid_outputs == previous.counters.invalid_outputs:
                        # Identical rework reuses its content hash, so it need not add an artifact.
                        accepted = state.artifacts.get(task.result_artifact_id or "")
                if accepted is not None:
                    log.append(
                        self.clock(),
                        "submission_accepted",
                        {"invocation_id": current.invocation_id, "payload": accepted.payload},
                    )
                elif invocation is not None:
                    # Reaching the retry limit closes the failed invocation too. Without
                    # this record, a scorecard would mistake its last refusal for acceptance.
                    log.append(self.clock(), "submission_refused", {"invocation_id": current.invocation_id})
            for waiver in state.waivers[len(previous.waivers) :]:
                if waiver.get("decided_by") == TRUST:
                    log.append(self.clock(), "review_skipped", waiver)
            if state.early_closure_reason and not previous.early_closure_reason:
                log.append(
                    self.clock(),
                    "run_closed_early",
                    {
                        "reason": state.early_closure_reason,
                        "decided_by": state.early_closed_by,
                        "not_needed_packages": [
                            p.id for p in state.packages.values() if p.status.value == "not_needed"
                        ],
                    },
                )
            self._log_rejection(state, current, log)
            self._note_qa_verdict(previous, state, current, log)
            self._note_lesson_decisions(previous, state, current, log)
            self._propose_org_changes(previous, state, log)
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
            if state.phase != before and state.phase in (RunPhase.DONE, RunPhase.FAILED):
                self._cleanup_scratch(state)
            self.ledger.save(state, log.head, self.clock())
            self._sync_company(state, log)
            if isinstance(current, UsageRecorded):
                # Agent CLIs print their final counts after submitting through MCP.
                # The index exported at submission must include that late usage too.
                if state.phase == RunPhase.DONE:
                    self._export(state)
                elif state.phase == before and state.phase in (RunPhase.BLOCKED, RunPhase.FAILED):
                    target = self.drafts_path(state.run_id)
                    if target is not None and target.is_dir():
                        self._export_drafts(state)
        return outward

    def _sync_company(self, state: RunState, log: AuditLog) -> None:
        """Work state follows the run. Lessons in a role's own notes go where they belong."""
        for entry in self.company.sync(state, self.org, self.clock()):
            try:
                if entry.kind == "lesson":
                    self.memory.write(entry.role, entry.text, source=entry.key)
                elif entry.kind == "team_lesson":
                    self._propose_memory(
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

    def _propose_memory(
        self, tier: str, owner: str, text: str, *, source_run: str, source: str, proposed_by: str, at: str
    ) -> bool:
        proposal, added, learned = self.company.record_memory(
            tier, owner, text, source_run=source_run, source=source, proposed_by=proposed_by, at=at
        )
        if learned and proposal.status == "accepted":
            reference = f"{source_run}: {source}, learned by {proposed_by} at {at}"
            # Approval belongs to the canonical lesson. A later paraphrase adds
            # evidence to it; a distinct lesson still waits for its own decision.
            if tier == "department":
                self.memory.write_department(self.org.departments[owner].head_ref, text, source=reference)
            else:
                self.memory.promote(self.org.root_role, text, reference)
        return added

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
            outside: list[str] = []
            held: list[str] = []
            if invocation.purpose in OUTSIDE_PURPOSES:
                outside = self.outside.record_lines(focus=state.goal)
            if invocation.purpose in (*OUTSIDE_PURPOSES, Purpose.INTEGRATE):
                held = [f"#{h.id}, from {h.role}: {h.command[:MAX_HELD_SHOWN]}"
                        + (f"\n{h.checks}" if h.checks else "")
                        for h in self.outside.held() if h.run_id == run_id]  # fmt: skip
            roster: list[str] = []
            record: list[str] = []
            if invocation.purpose == Purpose.RETROSPECTIVE:
                roster = org_growth.roster(self.org)
                try:
                    record = org_growth.signals(self.org, state, self.company, self.outside)
                    card = self.role_scorecard()
                    record.extend(card.lines())
                    for row in card.roles.values():
                        if row.evidence:
                            record.append(f"{row.role}: audit evidence: " + "; ".join(row.evidence[-3:]))
                except Exception as error:
                    self._log(run_id).append(self.clock(), "org_record_error", {"error": type(error).__name__})
            rules = [text for _, text in self.company.rules()]
        return Briefing(
            brief=brief,
            pending_lessons=lessons,
            team=team,
            outside=outside,
            held=held,
            roster=roster,
            org_record=record,
            rules=rules,
        )

    def role_scorecard(self) -> Scorecard:
        return scorecard(self.home, self.org)

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
                if not accepted and any(e.proposed_by != invocation.role for e in decided.evidence):
                    # A real head dropped a lesson about work it doesn't do, its hunter's drafts, and the same
                    # mistake came back in the next command. A head can't drop a teammate's lesson for good: it
                    # goes to the person, who decides.
                    sent = False
                    for learning in decided.evidence:
                        sent = (
                            self._propose_memory(
                                "company",
                                "company",
                                learning.text,
                                source_run=learning.source_run,
                                source=f"{DROPPED_BY}{invocation.role}; {learning.reference}",
                                proposed_by=learning.proposed_by,
                                at=self.clock(),
                            )
                            or sent
                        )
                    # The same words already proposed or decided for the whole company aren't asked about again.
                    if sent:
                        log.append(
                            self.clock(), "lesson_sent_to_person", {"number": number, "dropped_by": invocation.role}
                        )
                if accepted:
                    try:
                        for learning in decided.evidence:
                            self.memory.write_department(invocation.role, learning.text, source=learning.reference)
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

    def _propose_org_changes(self, previous: RunState, state: RunState, log: AuditLog) -> None:
        """A charter or retrospective was just accepted with proposed roster changes. A person decides
        each one, with `trypro org review`. Stored the moment they're accepted, so a run stopped later loses none."""
        for name in ("charter_artifact_id", "retrospective_artifact_id"):
            artifact_id = getattr(state, name)
            if not artifact_id or artifact_id == getattr(previous, name):
                continue
            artifact = state.artifacts.get(artifact_id)
            for change in (artifact.payload.get("org_changes") or []) if artifact else []:
                try:
                    if change["kind"] in ("retire_role", "merge_role"):
                        card = self.role_scorecard()
                        row = card.roles[f"{change['department']}/{change['role_id']}"]
                        # Preserve the measured evidence and its limitations for the person.
                        # A model's citations never replace the audit's snapshot.
                        lines = card.lines()
                        change = {
                            **change,
                            "scorecard_evidence": [
                                *lines[:2],
                                row.line(),
                                *row.evidence,
                                *lines[2 + len(card.roles) :],
                            ],
                        }
                    added = self.company.propose_org_change(
                        change, source_run=state.run_id, proposed_by=self.org.root_role, at=self.clock()
                    )
                except Exception as error:
                    log.append(self.clock(), "proposal_error", {"error": type(error).__name__})
                    continue
                log.append(
                    self.clock(),
                    "org_change_proposed",
                    {"kind": change["kind"], "role": f"{change['department']}/{change['role_id']}", "new": added},
                )

    def _propose_lessons(self, state: RunState) -> None:
        """New company lessons wait for the person. Repeats add evidence to the lesson already kept."""
        artifact = state.artifacts.get(state.retrospective_artifact_id or "")
        if artifact is None:
            return
        log = self._log(state.run_id)
        items = [*artifact.payload.get("lessons", []), *artifact.payload.get("procedures", [])]
        for text in items:
            self._propose_memory(
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
            elif kind == "tool_call" and data.get("fresh"):
                state.counters.fresh_actions += 1
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

    # ---- the outside: what the company put out, what came of it, and what waits for a person --------------------

    def _has_run(self, run_id: str) -> bool:
        return any(known == run_id for known, _ in self.ledger.list_runs())

    def track_outcome(
        self,
        run_id: str,
        role: str,
        url: str,
        *,
        kind: str = "",
        title: str = "",
        command: str = "",
        target: str = "",
    ) -> Outcome | None:
        """Start following something the company put out. Everyone who worked on the run shares what comes of it."""
        guessed_kind, guessed_title = describe_link(url, command)
        with self._lock:
            known = self._has_run(run_id)
            credited = [role]
            if known:
                state = self.ledger.load(run_id)
                credited += [task.owner_role for task in state.tasks.values() if task.owner_role]
                # Posting what an earlier command found credits who found it, too, when that command is named.
                runs = {run_id, *(found for found in RUN_ID.findall(f"{state.goal} {command}") if found != run_id)}
                for earlier in runs - {run_id}:
                    if self._has_run(earlier):
                        credited += [t.owner_role for t in self.ledger.load(earlier).tasks.values() if t.owner_role]
            item = self.outside.track(
                url,
                run_id=run_id,
                role=role,
                credited=credited,
                kind=kind or guessed_kind,
                title=title or guessed_title,
                at=self.clock(),
                target=target,
            )
            if item is not None and known:
                self.record(run_id, "outcome_tracked", {"id": item.id, "ref": item.ref, "role": role})
        return item

    def settle_outcome(self, outcome_id: int, status: str, note: str = "", *, by: str) -> Outcome | None:
        """What happened to it. A change becomes a lesson for every role that worked on it, and their heads decide
        whether it's kept for the whole department. Returns the outcome only if something changed."""
        with self._lock:
            item = self.outside.settle(outcome_id, status, note, self.clock())
            if item is None:
                return None
            lesson = outcome_lesson(item)
            for role in item.credited:
                if role not in self.org.roles:
                    continue
                # A lesson improves later runs, and failing to write one mustn't stop this.
                with contextlib.suppress(Exception):
                    self.memory.write(role, lesson)
            for department in sorted({role.split("/", 1)[0] for role in item.credited if role in self.org.roles}):
                self.company.propose_memory(
                    "department",
                    department,
                    lesson,
                    source_run=item.run_id,
                    source=f"outcome:{item.id}",
                    proposed_by=by,
                    at=self.clock(),
                )
            if self._has_run(item.run_id):
                self.record(
                    item.run_id,
                    "outcome_settled",
                    {"id": item.id, "status": status, "by": by, "role": item.role, "ref": item.ref},
                )
        return item

    def hold_command(self, run_id: str, role: str, command: str, checks: str = "") -> int:
        """A command that changes something outside, with nobody there to approve it. It waits for a person, with what
        code found about it first, such as issues that may already report the same thing."""
        with self._lock:
            number = self.outside.hold(
                run_id=run_id,
                role=role,
                command=command,
                workspace=self.workspace(run_id),
                at=self.clock(),
                checks=checks,
            )
            self.record(run_id, "command_held", {"id": number, "role": role, "command": command})
        return number

    def run_held(self, held_id: int, execute: Execute, *, by: str) -> Held | None:
        """A person approved a held command: run it where its role would have, and follow what it made."""
        item = self.outside.held_item(held_id)
        if item is None or not self.outside.decide_held(held_id, "running", by=by, at=self.clock()):
            return None
        try:
            output = execute(Path(item.workspace), item.command)
        except OSError as error:
            output = f"Could not run the command: {error}"
        if not self.outside.finish_held(held_id, output):
            return self.outside.held_item(held_id)
        if self._has_run(item.run_id):
            self.record(item.run_id, "held_command_ran", {"id": held_id, "by": by})
        for url in urls_in(output):
            self.track_outcome(item.run_id, item.role, url, command=item.command)
        return self.outside.held_item(held_id)

    def deny_held(self, held_id: int, *, by: str) -> bool:
        item = self.outside.held_item(held_id)
        if item is None or not self.outside.decide_held(held_id, "denied", by=by, at=self.clock()):
            return False
        if self._has_run(item.run_id):
            self.record(item.run_id, "held_command_denied", {"id": held_id, "by": by})
        return True

    def close(self) -> None:
        self.ledger.close()
        self.company.close()
        self.outside.close()
