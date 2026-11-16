"""Turn a finished run into files a person can open.

Citations are checked against what the run really retrieved, because a model can invent a source
as easily as a fact. Everything written here comes from the run's approved records.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Collection
from pathlib import Path
from typing import Any

from .pipeline.state import PackageStatus, RunState, TaskStatus
from .scratch import files_under

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_INPUTS = "inputs"

# What a machine makes for itself: never exported, never copied into a run. A role kept a virtualenv in its workspace
# under its own name ("venvs/llama_index"); exported and copied into the next run, its paths passed Windows' 260
# characters and the next run failed to start.
MADE_BY_A_MACHINE = frozenset({".trypro-scratch", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
                               ".mypy_cache", ".ruff_cache", ".tox", ".DS_Store", "site-packages",
                               "dist-packages"})  # fmt: skip


def environments(root: Path) -> set[Path]:
    """Python environments under root, whatever they're called: each has a pyvenv.cfg."""
    return {found.parent.relative_to(root) for found in files_under(root) if found.name == "pyvenv.cfg"}


def made_by_a_machine(relative: Path, found_environments: Collection[Path] = ()) -> bool:
    return any(part in MADE_BY_A_MACHINE for part in relative.parts) or any(
        relative.is_relative_to(environment) for environment in found_environments
    )


def read_sources(path: Path) -> dict[str, str]:
    """Everything this run retrieved, and the date reported for it."""
    if not path.is_file():
        return {}
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        found[record["source"]] = record.get("date", "")
    return found


def _citation(source: str, retrieved: dict[str, str]) -> str:
    return f"{source} ({'retrieved in this run' if source in retrieved else 'NOT retrieved in this run'})"


def _render_finding(finding: dict[str, Any], retrieved: dict[str, str]) -> list[str]:
    label = "**UNVERIFIED:** " if finding.get("unverified") else ""
    lines = [f"- {label}{finding['statement']} (confidence: {finding['confidence']})"]
    if finding.get("source_date"):
        lines.append(f"  - Source date: {finding['source_date']}")
    lines += [f"  - Source: {_citation(source, retrieved)}" for source in finding.get("sources", [])]
    return lines


def _render(artifact_type: str, payload: dict[str, Any], retrieved: dict[str, str]) -> list[str]:
    lines: list[str] = []
    if artifact_type == "PublicContent":
        lines += [f"Audience: {payload['audience']}", f"Channel: {payload['channel']}", f"Locale: {payload['locale']}"]
        lines += ["", payload["body"]]
        if payload.get("claims"):
            lines += ["", "## Claims"]
            lines += [f"- {c['claim']} ({c.get('source') or 'no source given'})" for c in payload["claims"]]
    elif artifact_type == "ResearchReport":
        lines += [f"Question: {payload['question']}", "", "## Findings"]
        for finding in payload.get("findings", []):
            lines += _render_finding(finding, retrieved)
    elif artifact_type == "CodeChange":
        lines += [payload["summary"], ""]
        lines += [f"- {item['path']}" for item in payload.get("files", [])]
    else:
        lines += [payload.get("summary", "")]
        if payload.get("outputs"):
            lines += ["", "## Outputs"]
            lines += [f"- {item}" for item in payload["outputs"]]
        if payload.get("open_issues"):
            lines += ["", "## Open issues"]
            lines += [f"- {item}" for item in payload["open_issues"]]
    return lines


def _review_notes(state: RunState) -> list[str]:
    """What approving reviewers still flagged. Without this, it never reached the person it was written for."""
    results = {t.result_artifact_id for t in state.tasks.values() if t.result_artifact_id}
    lines = []
    for artifact in state.artifacts.values():
        if artifact.type == "ReviewVerdict" and artifact.payload.get("subject_artifact_id") in results:
            who = f"{artifact.produced_by}, {artifact.payload['gate']}"
            lines += [f"- {issue['description']} ({who})" for issue in artifact.payload.get("issues", [])]
    return lines


def export_deliverables(state: RunState, workspace: Path, target: Path, retrieved: dict[str, str]) -> Path:
    """Write target/index.md, one file per task's result, and whatever roles wrote to the workspace."""
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    rows = []
    for task in state.tasks.values():
        artifact = state.artifacts.get(task.result_artifact_id or "")
        # The final deliverable is what the index itself reports.
        if artifact is None or artifact.type == "FinalDeliverable" or task.status == TaskStatus.NOT_NEEDED:
            continue
        name = _UNSAFE.sub("-", f"{task.owner_role.replace('/', '-')}-{task.task_id}") + ".md"
        body = [f"# {task.title}", "", f"By {task.owner_role}. Status: {task.status.value}.", ""]
        body += _render(artifact.type, artifact.payload, retrieved)
        (target / name).write_text("\n".join(body) + "\n", encoding="utf-8", newline="\n")
        gates = ", ".join(task.passed_gates) or "none"
        rows.append(f"| [{name}]({name}) | {task.owner_role} | {task.reviewer_role} | {gates} | {task.status.value} |")

    copied = []
    root = workspace.resolve()
    found = environments(root)
    for path in sorted(files_under(root)):
        relative = path.relative_to(root)
        if made_by_a_machine(relative, found):
            continue
        if path.is_file() and not path.is_symlink() and relative.parts[0] != _INPUTS:
            destination = target / "files" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            copied.append(relative.as_posix())

    final = state.artifacts.get(state.final_artifact_id or "")
    lines = ["# Deliverables", "", f"Goal: {state.goal}", f"Run: {state.run_id}", ""]
    if state.early_closure_reason:
        lines += [f"Closed early by {state.early_closed_by}: {state.early_closure_reason}", ""]
        lines += [
            f"- {p.id} ({p.title}): not_needed" for p in state.packages.values() if p.status == PackageStatus.NOT_NEEDED
        ]
        lines.append("")
    if final is not None:
        lines += [final.payload["summary_in_user_language"], "", "## Success criteria"]
        lines += [f"- [{'x' if c['met'] else ' '}] {c['criterion']}" for c in final.payload["criteria_results"]]
        if final.payload.get("known_limits"):
            lines += ["", "## Known limits"]
            lines += [f"- {item}" for item in final.payload["known_limits"]]
        lines.append("")
    lines += [
        "## Work",
        "",
        "| File | Made by | Reviewed by | Gates passed | Status |",
        "|---|---|---|---|---|",
        *rows,
        "",
    ]
    if copied:
        lines += ["## Other files roles wrote", ""]
        lines += [f"- [files/{name}](files/{name})" for name in copied]
        lines.append("")
    notes = _review_notes(state)
    if notes:
        lines += ["## What reviewers noted", "", *notes, ""]
    lines += ["## What this run retrieved", ""]
    lines += [f"- {source}{f' ({day})' if day else ''}" for source, day in sorted(retrieved.items())] or ["- Nothing."]
    lines += ["", *state.usage.lines()]
    index = target / "index.md"
    index.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return index


DRAFT_FOLDER = "draft"


# What a run stopped at, by the kind of question it asked, to finish "stopped at".
_STOPPED_AT = {
    "budget": "its token limit",
    "stuck": "a step that looked stuck",
    "escalation": "a question for a person",
    "gate_unavailable": "a review nobody could do",
    "runaway": "its limit on assignments",
    "stalled": "a point with no work it could start",
}


def stop_reason(state: RunState) -> str:
    """What a run stopped at, in a few words, to finish the label "NOT APPROVED: stopped at"."""
    if state.human_requests:
        request = next(iter(state.human_requests.values()))
        at = _STOPPED_AT.get(request.kind, "a question for a person")
        if request.kind in ("budget", "stuck"):
            total = state.usage.estimated_tool_tokens if request.usage_is_tool_traffic else state.usage.total
            kind = "estimated tool tokens" if request.usage_is_tool_traffic else "model tokens"
            return f"{at}, at {total:,} {kind}"
        return at
    return "a failure" if state.failure_reason else "a stop"


def stop_detail(state: RunState) -> str:
    """Why a run stopped, in full: its open question, or why it failed."""
    if state.human_requests:
        return " ".join(next(iter(state.human_requests.values())).question.split())
    return state.failure_reason or "It stopped."


def export_drafts(state: RunState, workspace: Path, target: Path, retrieved: dict[str, str]) -> Path:
    """When a run stops before it's done, a person still gets what it made: labelled, and with what reviewers said."""
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    reason = stop_reason(state)
    label = f"NOT APPROVED: stopped at {reason}"
    rows = []
    for task in state.tasks.values():
        artifact = state.artifacts.get(task.result_artifact_id or "")
        if artifact is None or artifact.type == "FinalDeliverable":
            continue
        name = _UNSAFE.sub("-", f"{task.owner_role.replace('/', '-')}-{task.task_id}") + ".md"
        body = [f"# {label}", "", f"## {task.title}", "", f"By {task.owner_role}. Status: {task.status.value}.", ""]
        body += _render(artifact.type, artifact.payload, retrieved)
        said = _verdicts_on(state, artifact.artifact_id)
        if said:
            body += ["", "## What reviewers said", "", *said]
        (target / name).write_text("\n".join(body) + "\n", encoding="utf-8", newline="\n")
        rows.append(
            f"| [{name}]({name}) | {task.owner_role} | {task.status.value} | {', '.join(task.passed_gates) or 'none'} |"
        )
    copied = _copy_workspace(workspace, target)
    lines = [
        f"# {label}",
        "",
        f"Goal: {state.goal}",
        f"Run: {state.run_id}",
        "",
        "This is what the company made before it stopped. Nobody approved it, so read it as a draft. "
        "If the run goes on and finishes, the approved work replaces this folder.",
        "",
        "## Why it stopped",
        "",
        stop_detail(state),
        "",
        "## Work",
        "",
        "| File | Made by | Status | Reviews passed |",
        "|---|---|---|---|",
        *(rows or ["| Nothing was handed in yet. | | | |"]),
        "",
    ]
    if copied:
        lines += ["## Files roles wrote", ""]
        lines += [f"- [files/{name}](files/{name})" for name in copied]
    lines += ["", *state.usage.lines()]
    index = target / "index.md"
    index.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return index


def _verdicts_on(state: RunState, artifact_id: str) -> list[str]:
    lines = []
    for artifact in state.artifacts.values():
        if artifact.type == "ReviewVerdict" and artifact.payload.get("subject_artifact_id") == artifact_id:
            who = f"{artifact.produced_by}, {artifact.payload['gate']}: {artifact.payload['decision']}"
            lines += [
                f"- {issue['severity']}: {issue['description']} ({who})" for issue in artifact.payload.get("issues", [])
            ]
    return lines


def _copy_workspace(workspace: Path, target: Path) -> list[str]:
    copied: list[str] = []
    root = workspace.resolve()
    if not root.is_dir():
        return copied
    for path in sorted(files_under(root)):
        relative = path.relative_to(root)
        if path.is_file() and not path.is_symlink() and relative.parts[0] != _INPUTS:
            destination = target / "files" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            copied.append(relative.as_posix())
    return copied
