"""Designing a company with the client's own model, over MCP, with no model key here.

The same checks apply as when Try Professional calls a model itself. This only changes who holds the model: each step
hands out the work and validates what comes back. A person still approves the result, at a terminal.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from ..adapters.api_runner.clients import ChatRequest, ClientError, ToolResult
from ..config import load_env_file
from ..core.audit import AuditLog
from ..memory.embedding import Embedder
from ..org import load_bundle
from ..skills.rerank import RerankCache, record_judgment
from .architect import MAX_ATTEMPTS, _settle, check_proposal, submit_spec, system_prompt, user_prompt
from .compiler import ProposalError, compile_org
from .flow import _empty_tokens, _finish, _now, _wait_for_room, load_proposal, proposals_dir
from .proposal import OrgProposal
from .provision import GITHUB_TOKEN_HELP, github_token_is_set
from .review import render
from .templates import load_template, rank_templates

CLIENT_MODEL = "the client's model"
MAX_JUDGMENT_ATTEMPTS = 2
_META = "meta.json"
_PENDING = "pending_judgments.json"


class PullError(RuntimeError):
    """A refusal the client can read and act on."""


class _AwaitingJudge:
    """Stands where a model would be. Each judgment still needed is collected, and handed to the client."""

    def __init__(self) -> None:
        self.pending: dict[str, dict[str, Any]] = {}

    def user_message(self, text: str) -> Any:
        return {"role": "user", "content": text}

    def tool_results_messages(self, results: list[ToolResult]) -> list[Any]:
        return []

    def complete(self, request: ChatRequest) -> Any:
        self.pending[request.metadata["role"]] = {
            "instructions": request.system,
            "assignment": request.messages[0]["content"],
            "candidates": request.metadata["candidates"],
            "output_schema": request.tools[0].input_schema,
        }
        raise ClientError("Waiting for the client's model.", retryable=False)


def _path(root: Path, proposal_id: str) -> Path:
    path = proposals_dir(root) / proposal_id
    if not (path / _META).is_file():
        raise PullError(f"There's no proposal '{proposal_id}'. Call org_propose.")
    return path


def _read_meta(path: Path) -> dict[str, Any]:
    meta: dict[str, Any] = json.loads((path / _META).read_text(encoding="utf-8"))
    return meta


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")


def _note(path: Path, kind: str, data: dict[str, Any]) -> None:
    AuditLog(path / "audit.jsonl").append(_now(), kind, data)


def _design_action(path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    template = load_template(meta["template"])
    return {
        "proposal_id": path.name,
        "status": "needs_design",
        "template": template.id,
        "instructions": system_prompt(),
        "assignment": user_prompt(meta["description"], template, None, ""),
        "output_schema": submit_spec().input_schema,
        "submit_with": "org_proposal_submit",
    }


def begin_design(
    root: Path,
    embedder: Embedder,
    description: str,
    *,
    company: str | None = None,
    exclude_templates: tuple[str, ...] = (),
    say: Callable[[str], None] = lambda text: None,
) -> dict[str, Any]:
    if not description.strip():
        raise PullError("A company needs a description of the business.")
    _wait_for_room(embedder, say)
    template_id, match = rank_templates(description, embedder, exclude=exclude_templates)[0]
    path = proposals_dir(root) / f"prop-{uuid.uuid4().hex[:10]}"
    path.mkdir(parents=True)
    meta: dict[str, Any] = {
        "description": description,
        "template": template_id,
        "template_match": round(match, 3),
        "created_at": _now(),
        "status": "designing",
        "model": CLIENT_MODEL,
        "company": company,
        "design_attempts": 0,
        "tokens": {"architect": _empty_tokens(), "rerank": {"total": 0, "calls": 0}},
        "regenerations": 0,
    }
    _write_json(path / _META, meta)
    _note(path, "proposal_started", {"description": description, "template": template_id, "model": CLIENT_MODEL})
    action = _design_action(path, meta)
    load_env_file(root)
    if not github_token_is_set():
        # Before a client spends anything on the design, so a person can set it first.
        action = {"tell_the_person_first": GITHUB_TOKEN_HELP, **action}
    return action


def submit_design(
    root: Path,
    embedder: Embedder,
    proposal_id: str,
    arguments: Any,
    *,
    github: Any = None,
    say: Callable[[str], None] = lambda text: None,
) -> dict[str, Any]:
    path = _path(root, proposal_id)
    meta = _read_meta(path)
    if meta["status"] != "designing":
        raise PullError(f"This proposal isn't waiting for a design. Its status is {meta['status']}.")
    template = load_template(meta["template"])
    meta["design_attempts"] += 1
    try:
        proposal, _ = check_proposal(template, arguments)
        proposal, notes = _settle(proposal, template, meta["description"], True)
        if meta.get("company"):
            proposal = proposal.model_copy(update={"company_name": meta["company"]})
        compiled = compile_org(template, proposal)
    except ProposalError as error:
        _note(path, "architect_rejected", {"attempt": meta["design_attempts"], "reason": str(error)})
        if meta["design_attempts"] >= MAX_ATTEMPTS:
            meta["status"] = "failed"
            _write_json(path / _META, meta)
            return {"proposal_id": proposal_id, "status": "failed", "reason": f"Last problem: {error}"}
        _write_json(path / _META, meta)
        return {**_design_action(path, meta), "status": "rejected", "reason": str(error)}
    meta["design_notes"] = notes
    meta["status"] = "judging"
    departments = len(compiled.org["departments"])
    _note(path, "architect_accepted", {"attempt": meta["design_attempts"], "departments": departments})
    judge = _AwaitingJudge()
    bundle = _finish(
        path, meta, proposal, compiled, root, embedder,
        client=judge, model=CLIENT_MODEL, discovery=True, github=github, say=say,
    )  # fmt: skip
    meta["discovery_report"] = bundle.provisioned.discovery
    return _after_provisioning(root, embedder, path, meta, judge)


def _after_provisioning(
    root: Path, embedder: Embedder, path: Path, meta: dict[str, Any], judge: _AwaitingJudge
) -> dict[str, Any]:
    attempts = meta.setdefault("judgment_attempts", {})
    pending = {role: work for role, work in judge.pending.items() if attempts.get(role, 0) < MAX_JUDGMENT_ATTEMPTS}
    _write_json(path / _PENDING, pending)
    if pending:
        meta["status"] = "judging"
        _write_json(path / _META, meta)
        return _judgment_action(path, pending)
    meta["status"] = "proposed"
    _write_json(path / _META, meta)
    bundle = load_proposal(root, path.name)
    bundle.provisioned.discovery = meta.get("discovery_report", bundle.provisioned.discovery)
    bundle.save()
    return {
        "proposal_id": path.name,
        "status": "ready_for_a_person",
        "review": render(bundle),
        "needs_a_person": f"A person approves this at a terminal: trypro init --review {path.name}",
    }


def _judgment_action(path: Path, pending: dict[str, Any]) -> dict[str, Any]:
    role = sorted(pending)[0]
    work = pending[role]
    return {
        "proposal_id": path.name,
        "status": "needs_judgment",
        "role": role,
        "roles_left": len(pending),
        "instructions": work["instructions"],
        "assignment": work["assignment"],
        "output_schema": work["output_schema"],
        "submit_with": "org_skill_judgment_submit",
    }


def submit_judgment(
    root: Path,
    embedder: Embedder,
    proposal_id: str,
    role_ref: str,
    decisions: Any,
    *,
    say: Callable[[str], None] = lambda text: None,
) -> dict[str, Any]:
    path = _path(root, proposal_id)
    meta = _read_meta(path)
    if meta["status"] != "judging":
        raise PullError(f"This proposal isn't waiting for judgments. Its status is {meta['status']}.")
    pending: dict[str, Any] = json.loads((path / _PENDING).read_text(encoding="utf-8"))
    if role_ref not in pending:
        raise PullError(f"'{role_ref}' has no judgment waiting.")
    raw = yaml.safe_load((path / "proposal.yaml").read_text(encoding="utf-8"))
    template = load_template(meta["template"])
    proposal = OrgProposal.model_validate(raw)
    compiled = compile_org(template, proposal)
    if meta.get("description"):
        compiled.org["company"]["description"] = meta["description"]
    org = load_bundle(compiled.org, compiled.roles)
    if not isinstance(decisions, list):
        raise PullError("decisions must be a list, with one decision for each candidate, by its number.")
    cache = RerankCache(path / "rerank.sqlite")
    try:
        candidates = pending[role_ref]["candidates"]
        answered = record_judgment(org.role(role_ref), candidates, decisions, CLIENT_MODEL, org.business.text, cache)
    finally:
        cache.close()
    attempts = meta.setdefault("judgment_attempts", {})
    attempts[role_ref] = attempts.get(role_ref, 0) + 1
    _note(path, "judgment", {"role": role_ref, "answered": answered, "candidates": len(candidates)})
    del pending[role_ref]
    if pending:
        _write_json(path / _PENDING, pending)
        _write_json(path / _META, meta)
        return _judgment_action(path, pending)
    _wait_for_room(embedder, say)
    judge = _AwaitingJudge()
    discovered = meta.get("discovery_report", [])
    _finish(
        path, meta, proposal, compiled, root, embedder,
        client=judge, model=CLIENT_MODEL, discovery=False, github=None, say=say,
    )  # fmt: skip
    meta["discovery_report"] = discovered
    return _after_provisioning(root, embedder, path, meta, judge)


def proposal_status(root: Path, proposal_id: str) -> dict[str, Any]:
    """What this proposal needs next. Safe to call repeatedly."""
    path = _path(root, proposal_id)
    meta = _read_meta(path)
    if meta["status"] == "designing":
        return _design_action(path, meta)
    if meta["status"] == "judging":
        if not (path / _PENDING).is_file():
            # Stopped while it was searching for skills. Nothing was activated, and the design can be redone.
            reason = "This proposal was interrupted while finding skills. Call org_propose to start again."
            return {"proposal_id": proposal_id, "status": "failed", "reason": reason}
        return _judgment_action(path, json.loads((path / _PENDING).read_text(encoding="utf-8")))
    if meta["status"] == "failed":
        return {"proposal_id": proposal_id, "status": "failed"}
    if meta["status"] == "approved":
        return {"proposal_id": proposal_id, "status": "approved"}
    return {
        "proposal_id": proposal_id,
        "status": "ready_for_a_person",
        "review": render(load_proposal(root, proposal_id)),
        "needs_a_person": f"A person approves this at a terminal: trypro init --review {proposal_id}",
    }


__all__ = ["CLIENT_MODEL", "PullError", "begin_design", "proposal_status", "submit_design", "submit_judgment"]
