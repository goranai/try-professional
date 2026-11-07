"""Turn a finished run into files a person can open.

Citations are checked against what the run really retrieved, because a model can invent a source
as easily as a fact. Everything written here comes from the run's approved records.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from .pipeline.state import RunState

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_INPUTS = "inputs"


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
        if artifact is None or artifact.type == "FinalDeliverable":
            continue
        name = _UNSAFE.sub("-", f"{task.owner_role.replace('/', '-')}-{task.task_id}") + ".md"
        body = [f"# {task.title}", "", f"By {task.owner_role}. Status: {task.status.value}.", ""]
        body += _render(artifact.type, artifact.payload, retrieved)
        (target / name).write_text("\n".join(body) + "\n", encoding="utf-8", newline="\n")
        gates = ", ".join(task.passed_gates) or "none"
        rows.append(f"| [{name}]({name}) | {task.owner_role} | {task.reviewer_role} | {gates} | {task.status.value} |")

    copied = []
    root = workspace.resolve()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_file() and not path.is_symlink() and relative.parts[0] != _INPUTS:
            destination = target / "files" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            copied.append(relative.as_posix())

    final = state.artifacts.get(state.final_artifact_id or "")
    lines = ["# Deliverables", "", f"Goal: {state.goal}", f"Run: {state.run_id}", ""]
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
    if state.usage.estimated:
        lines += [
            "",
            f"Tokens: about {state.usage.total}, counted by the server from what passed through it. "
            "Your client's own count is higher, often by several times, and it's the one that's billed.",
        ]
    else:
        lines += ["", f"Tokens: {state.usage.total} total, in {state.usage.calls} model calls."]
    index = target / "index.md"
    index.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return index
