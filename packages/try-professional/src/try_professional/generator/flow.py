"""From a description to a proposal, and from an approved proposal to a company.

A proposal lives in .trypro/proposals/<id>/ and changes nothing else. Only activate() writes org/,
skills.lock, and skills/decisions.yaml, and only the review screen calls it.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from skill_customs import Lockfile, render_attribution, skill_dir, verify

from ..core.audit import AuditLog
from ..memory.embedding import Embedder, model_is_loaded
from ..org import Org, load_bundle, load_org
from ..resources import GENERATION_AFTER_MODEL_LOADED_MB, GENERATION_PEAK_MB, wait_for_memory
from ..skills import Decisions, LoadoutStore, SkillCatalog, match_all, sync_catalog
from .architect import ArchitectFailed, ArchitectResult, check_proposal, run_architect
from .compiler import CompiledOrg, ProposalError, compile_org
from .estimate import Estimate, estimate_run
from .proposal import OrgProposal
from .provision import ProvisionResult, provision
from .templates import Template, load_template, rank_templates

ATTRIBUTION = "THIRD_PARTY_SKILLS.md"
MEANINGFUL_MATCH = 0.45


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ActivationError(RuntimeError):
    """The proposal can't be activated. Nothing was changed."""


@dataclass
class Proposal:
    path: Path
    meta: dict[str, Any]
    compiled: CompiledOrg
    provisioned: ProvisionResult
    estimate: Estimate
    notes: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.path.name

    @property
    def org(self) -> Org:
        return load_bundle(self.compiled.org, self.compiled.roles)

    @property
    def template(self) -> Template:
        return load_template(self.meta["template"])

    @property
    def architect_tokens(self) -> int:
        return int(self.meta["tokens"]["architect"]["total"])

    def audit(self, kind: str, data: dict[str, Any]) -> None:
        log_path = self.path / "audit.jsonl"
        AuditLog(log_path).append(_now(), kind, data)

    def release_unpacked(self) -> None:
        """Delete what this proposal unpacked. skills.lock still pins each skill to its commit."""
        shutil.rmtree(self.path / "skills", ignore_errors=True)
        (self.path / "catalog.sqlite").unlink(missing_ok=True)

    def save(self) -> None:
        if (self.path / "org").exists():
            shutil.rmtree(self.path / "org")
        self.compiled.write(self.path / "org")
        (self.path / "meta.json").write_text(
            json.dumps(self.meta, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n"
        )
        (self.path / "provisioned.json").write_text(
            json.dumps(self.provisioned.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8", newline="\n"
        )
        (self.path / "estimate.json").write_text(json.dumps(self.estimate.to_dict()), encoding="utf-8", newline="\n")


def proposals_dir(root: Path) -> Path:
    return root / ".trypro" / "proposals"


def _empty_tokens() -> dict[str, Any]:
    return {"total": 0, "input": 0, "output": 0, "cached": 0, "calls": 0}


def _add_architect(meta: dict[str, Any], result: ArchitectResult) -> None:
    tokens = meta["tokens"]["architect"]
    tokens["total"] += result.total_tokens
    tokens["input"] += result.input_tokens
    tokens["output"] += result.output_tokens
    tokens["cached"] += result.cached_input_tokens
    tokens["calls"] += result.calls


def _finish(
    path: Path,
    meta: dict[str, Any],
    proposal: OrgProposal,
    compiled: CompiledOrg,
    root: Path,
    embedder: Embedder,
    *,
    client: Any,
    model: str,
    discovery: bool,
    github: Any,
    say: Callable[[str], None],
    provisioning: bool = True,
) -> Proposal:
    (path / "proposal.yaml").write_text(
        yaml.safe_dump(proposal.model_dump(mode="json", exclude_defaults=True), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="\n",
    )
    org = load_bundle(compiled.org, compiled.roles)
    if not provisioning:
        provisioned = ProvisionResult(discovery=["Skills weren't matched for this proposal."])
        bundle = Proposal(path, meta, compiled, provisioned, estimate_run(org), compiled.notes)
        bundle.save()
        return bundle
    provisioned = provision(
        org,
        path,
        root,
        embedder,
        client=client,
        model=model,
        discovery=discovery,
        github=github,
        say=say,
        sleep=(lambda seconds: None) if github is not None else time.sleep,
    )
    meta["tokens"]["rerank"]["total"] += provisioned.rerank.get("tokens", 0)
    meta["tokens"]["rerank"]["calls"] += provisioned.rerank.get("calls", 0)
    bundle = Proposal(path, meta, compiled, provisioned, estimate_run(org), compiled.notes)
    bundle.save()
    return bundle


def _wait_for_room(embedder: Embedder, say: Callable[[str], None]) -> None:
    loaded = model_is_loaded(embedder.model_name)
    wait_for_memory(GENERATION_AFTER_MODEL_LOADED_MB if loaded else GENERATION_PEAK_MB, say=say)


def create_proposal(
    root: Path,
    embedder: Embedder,
    *,
    description: str = "",
    template_id: str | None = None,
    company: str | None = None,
    client: Any = None,
    model: str = "",
    discovery: bool = True,
    github: Any = None,
    exclude_templates: tuple[str, ...] = (),
    say: Callable[[str], None] = lambda text: None,
    provisioning: bool = True,
) -> Proposal:
    """Choose a template, adapt it if there's a model, and find skills. Changes nothing outside the proposal.

    provisioning=False stops after the design, with no skills matched and no re-rank spending.
    """
    _wait_for_room(embedder, say)
    if template_id:
        match = None
    else:
        ranked = rank_templates(description, embedder, exclude=exclude_templates)
        template_id, match = ranked[0]
    template = load_template(template_id)
    path = proposals_dir(root) / f"prop-{uuid.uuid4().hex[:10]}"
    path.mkdir(parents=True)
    meta: dict[str, Any] = {
        "description": description,
        "template": template.id,
        "template_match": round(match, 3) if match is not None else None,
        "created_at": _now(),
        "status": "proposed",
        "model": model,
        "tokens": {"architect": _empty_tokens(), "rerank": {"total": 0, "calls": 0}},
        "regenerations": 0,
    }
    name = company or root.name

    def note(kind: str, data: dict[str, Any]) -> None:
        AuditLog(path / "audit.jsonl").append(_now(), kind, data)

    note("proposal_started", {"description": description, "template": template.id, "model": model})
    if client is not None and description:
        say(f"Designing the company with {model}, starting from the {template.name} template.")
        result = run_architect(description, template, client, model, note=note)
        _add_architect(meta, result)
        proposal, compiled = result.proposal, result.compiled
        if company:
            proposal = proposal.model_copy(update={"company_name": company})
            compiled = compile_org(template, proposal)
    else:
        proposal = template.keep_all(name)
        compiled = compile_org(template, proposal)
    return _finish(
        path, meta, proposal, compiled, root, embedder,
        client=client, model=model, discovery=discovery, github=github, say=say, provisioning=provisioning,
    )  # fmt: skip


def load_proposal(root: Path, proposal_id: str) -> Proposal:
    path = proposals_dir(root) / proposal_id
    if not (path / "meta.json").is_file():
        raise FileNotFoundError(f"no proposal '{proposal_id}' in {proposals_dir(root)}")
    meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    raw = yaml.safe_load((path / "proposal.yaml").read_text(encoding="utf-8"))
    template = load_template(meta["template"])
    proposal = OrgProposal.model_validate(raw)
    compiled = compile_org(template, proposal)
    provisioned = ProvisionResult.from_dict(json.loads((path / "provisioned.json").read_text(encoding="utf-8")))
    estimate = Estimate(**json.loads((path / "estimate.json").read_text(encoding="utf-8")))
    return Proposal(path, meta, compiled, provisioned, estimate, compiled.notes)


def regenerate(
    bundle: Proposal,
    root: Path,
    embedder: Embedder,
    reason: str,
    *,
    client: Any,
    model: str,
    discovery: bool = True,
    github: Any = None,
    say: Callable[[str], None] = lambda text: None,
) -> Proposal:
    """Another paid design call, told what the person wants changed."""
    _wait_for_room(embedder, say)
    raw = yaml.safe_load((bundle.path / "proposal.yaml").read_text(encoding="utf-8"))
    previous = OrgProposal.model_validate(raw)
    bundle.audit("regenerate", {"reason": reason})
    result = run_architect(
        bundle.meta["description"],
        bundle.template,
        client,
        model,
        previous=previous,
        feedback=reason,
        note=bundle.audit,
    )
    bundle.meta["regenerations"] += 1
    _add_architect(bundle.meta, result)
    return _finish(
        bundle.path, bundle.meta, result.proposal, result.compiled, root, embedder,
        client=client, model=model, discovery=discovery, github=github, say=say,
    )  # fmt: skip


def apply_edit(
    bundle: Proposal,
    root: Path,
    embedder: Embedder,
    *,
    client: Any,
    model: str,
    discovery: bool = False,
    github: Any = None,
    say: Callable[[str], None] = lambda text: None,
) -> Proposal:
    """Revalidate what a person changed in proposal.yaml. Raises ProposalError if it can't be used."""
    raw = yaml.safe_load((bundle.path / "proposal.yaml").read_text(encoding="utf-8"))
    proposal, compiled = check_proposal(bundle.template, raw)
    bundle.audit("edited", {"by": "person"})
    return _finish(
        bundle.path, bundle.meta, proposal, compiled, root, embedder,
        client=client, model=model, discovery=discovery, github=github, say=say,
    )  # fmt: skip


def _copy_approved(bundle: Proposal, root: Path, approved: set[str]) -> None:
    """Move the skills that were approved into the project, and leave everything else behind."""
    source = Lockfile.load(bundle.path / "skills.lock")
    target = Lockfile.load(root / "skills.lock")
    changed = False
    for skill_id, entry in source.entries.items():
        if skill_id not in approved or entry.trust == "quarantined":
            continue
        destination = skill_dir(entry, root / "skills")
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill_dir(entry, bundle.path / "skills"), destination)
        target.add(entry)
        changed = True
    target.save()
    if changed:
        (root / ATTRIBUTION).write_text(render_attribution(target), encoding="utf-8", newline="\n")
    problems = verify(target, dest=root / "skills")
    if problems:
        raise ActivationError("Installed skills don't match skills.lock: " + "; ".join(str(p) for p in problems[:5]))


def activate(bundle: Proposal, root: Path, embedder: Embedder, *, by: str) -> Org:
    """Make the proposal the company. A person decided this, at a terminal. See review.py."""
    issues = bundle.compiled.issues()
    if issues:
        raise ActivationError("; ".join(str(issue) for issue in issues))
    if bundle.meta.get("status") == "approved":
        raise ActivationError("This proposal was already approved.")
    org_dir = root / "org"
    if (org_dir / "org.yaml").exists():
        raise ActivationError(f"{org_dir} already has a company. Move it first: nothing was changed.")

    approved = {match.skill_id for matches in bundle.provisioned.matches.values() for match in matches}
    _copy_approved(bundle, root, approved)
    bundle.compiled.write(org_dir)
    org = load_org(org_dir)

    decisions = Decisions(root / "skills" / "decisions.yaml")
    for role, matches in bundle.provisioned.matches.items():
        for match in matches:
            if match.category_source == "proposed":
                decisions.assign_category(match.skill_id, match.category, by=by)
            decisions.decide(role, match.skill_id, "approved", by=by, content_sha256=match.content_sha256)
    decisions.save()

    skills_home = root / ".trypro" / "skills"
    skills_home.mkdir(parents=True, exist_ok=True)
    if (bundle.path / "rerank.sqlite").is_file():
        shutil.copyfile(bundle.path / "rerank.sqlite", skills_home / "rerank.sqlite")
    catalog = SkillCatalog(skills_home / "catalog.sqlite", embedder)
    store = LoadoutStore(skills_home / "loadouts.sqlite")
    try:
        sync_catalog(catalog, Lockfile.load(root / "skills.lock"), root / "skills", decisions)
        match_all(org, catalog, store, decisions)
    finally:
        catalog.close()
        store.close()

    bundle.meta["status"] = "approved"
    bundle.meta["approved_by"] = by
    bundle.meta["approved_at"] = _now()
    bundle.save()
    bundle.release_unpacked()
    bundle.audit("approved", {"by": by, "skills": len(approved), "roles": len(org.roles)})
    return org


__all__ = [
    "ActivationError",
    "ArchitectFailed",
    "Proposal",
    "ProposalError",
    "activate",
    "apply_edit",
    "create_proposal",
    "load_proposal",
    "regenerate",
]
