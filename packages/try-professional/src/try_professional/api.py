"""The stable Python API: create or open a company, add roles, run a command, and read its results.

Everything in this module is **stable**: it keeps working across 0.x minor versions, and a change that breaks it is
announced in the CHANGELOG one minor version ahead. Everything else in the package is internal, or experimental,
unless docs/building-on.md says otherwise.

    from try_professional import Company

    company = Company.create("my-company", name="Acme")          # the default company, with no model or cost
    company.add_role("marketing", "newsletter-editor", title="Newsletter Editor",
                     mission="Write the weekly newsletter.", skill_needs=["write email newsletters"])
    result = company.run("Write a launch note", dry_run=True)     # a scripted model stands in: free
    print(result.status, company.deliverables(result.run_id))

Two things stay with a person, by design, and this API doesn't do them: approving a company the AI designed
(`trypro init --review`), and answering a run's questions (`trypro decide`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .adapters.api_runner.runner import ApiRunner, ModelRoutes, RunResult
from .adapters.api_runner.scripted import ScriptedClient
from .config import CONFIG_NAME, CONFIG_TEMPLATE, load_config
from .core.orchestrator import Orchestrator
from .org import Org, load_org, validate_bundle
from .org.loader import read_documents
from .packs import available_packs, default_packs, install_org

__all__ = ["Company", "CompanyError", "RoleInfo", "RunResult", "RunStatus"]

ROLE_DEFAULTS: dict[str, Any] = {
    "inputs": ["TaskBrief"],
    "outputs": ["PublicContent", "TaskReport"],
    "allowed_tools": [
        "workspace_read",
        "workspace_write",
        "workspace_list",
        "memory_recall",
        "memory_write",
        "skill_search",
        "skill_get",
    ],
    "required_tools": ["workspace_write"],
    "forbidden_tools": [],
    "model_tier": "standard",
    "max_skills": 8,
}


class CompanyError(Exception):
    """Something about the company stopped the request. The message says what, and nothing was changed."""


@dataclass(frozen=True)
class RoleInfo:
    ref: str
    title: str
    mission: str
    is_head: bool


@dataclass(frozen=True)
class RunStatus:
    run_id: str
    goal: str
    phase: str
    questions: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def finished(self) -> bool:
        return self.phase in ("done", "failed")


class Company:
    """A company in a folder: its roles in `org/`, its settings in `trypro.yaml`, and its records in `.trypro/`."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        if not (self.root / "org" / "org.yaml").is_file():
            raise CompanyError(f"No company in {self.root}. Create one with Company.create, or trypro init.")

    # ---- creating and opening ----------------------------------------------------------------------------------

    @classmethod
    def create(cls, root: str | Path, *, name: str | None = None, packs: list[str] | None = None) -> Company:
        """The default company, built from packs of ready-made departments, with no Legal unless `packs` names
        it. No model is called, and nothing is spent. It has no skills until you add some (`trypro skills`). A company
        the AI designs from a description comes from `trypro init "<description>"` instead, because a person approves
        it."""
        folder = Path(root).resolve()
        if (folder / "org" / "org.yaml").exists():
            raise CompanyError(f"{folder} already has a company. Open it with Company(path).")
        chosen = packs or default_packs()
        unknown = sorted(set(chosen) - set(available_packs()))
        if unknown:
            raise CompanyError(f"Unknown packs: {', '.join(unknown)}. Available: {', '.join(available_packs())}")
        folder.mkdir(parents=True, exist_ok=True)
        install_org(folder / "org", name or folder.name, chosen)
        (folder / CONFIG_NAME).write_text(CONFIG_TEMPLATE, encoding="utf-8", newline="\n")
        home = folder / ".trypro"
        home.mkdir(exist_ok=True)
        (home / ".gitignore").write_text("*\n", encoding="utf-8", newline="\n")
        return cls(folder)

    @classmethod
    def open(cls, root: str | Path) -> Company:
        return cls(Path(root))

    @property
    def org(self) -> Org:
        """The company as loaded and validated. Read it; change roles with add_role, or by editing org/."""
        return load_org(self.root / "org")

    # ---- roles ---------------------------------------------------------------------------------------------------

    def roles(self) -> list[RoleInfo]:
        org = self.org
        return [RoleInfo(ref, role.title, role.mission, org.is_head(ref)) for ref, role in org.roles.items()]

    def add_role(
        self,
        department: str,
        role_id: str,
        *,
        title: str,
        mission: str,
        responsibilities: list[str] | None = None,
        skill_needs: list[str] | None = None,
        skill_categories: list[str] | None = None,
        instructions: str = "",
        **overrides: Any,
    ) -> RoleInfo:
        """Add a role to an existing department. It reports to the department's head, who also reviews its work.
        Any role field can be set through `overrides`, as in `docs/spec/role-v1.md`. If the result doesn't
        validate, nothing is written, and CompanyError says why."""
        org_path = self.root / "org" / "org.yaml"
        data = yaml.safe_load(org_path.read_text(encoding="utf-8"))
        departments = {d["id"]: d for d in data["departments"]}
        if department not in departments:
            raise CompanyError(f"No department '{department}'. There are: {', '.join(departments)}")
        dept = departments[department]
        if role_id in dept["roles"]:
            raise CompanyError(f"{department}/{role_id} already exists.")
        head = f"{department}/{dept['head']}"
        role = {
            "spec": "role/v1",
            "id": role_id,
            "title": title,
            "department": department,
            "mission": mission,
            "responsibilities": responsibilities or [mission],
            **ROLE_DEFAULTS,
            "allowed_skill_categories": skill_categories or [],
            "escalation": head,
            "reviewer": head,
            "skill_needs": skill_needs or [],
            "system_prompt": instructions or f"You are the {title}. {mission}",
            **overrides,
        }
        role_path = self.root / "org" / "roles" / department / f"{role_id}.yaml"
        before = org_path.read_text(encoding="utf-8")
        dept["roles"].append(role_id)
        role_path.parent.mkdir(parents=True, exist_ok=True)
        role_path.write_text(yaml.safe_dump(role, sort_keys=False, allow_unicode=True), encoding="utf-8")
        org_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        problems = self.validate()
        if problems:
            role_path.unlink()
            org_path.write_text(before, encoding="utf-8")
            raise CompanyError("The role wasn't added: " + "; ".join(problems))
        added = self.org.role(f"{department}/{role_id}")
        return RoleInfo(added.ref, added.title, added.mission, False)

    def validate(self) -> list[str]:
        """Every error that would stop the company loading. An empty list means it's valid."""
        org, roles = read_documents(self.root / "org")
        return [str(issue) for issue in validate_bundle(org, roles) if issue.severity == "error"]

    # ---- Claude Code ---------------------------------------------------------------------------------------------

    def setup_claude_code(self, *, sandboxed: bool = False) -> list[Path]:
        """Write what Claude Code needs to run this company: the /company command, one subagent per role, each bound
        to its own role's server, and .mcp.json. Each role is a full agent, with the shell, files, git, and the web,
        unless `sandboxed`. Run it again after changing roles. Returns the files written."""
        from .adapters.mcp.claude_code import write_claude_code_files

        write_claude_code_files(self.root, self.org, sandboxed=sandboxed)
        claude = self.root / ".claude"
        return sorted([*claude.rglob("*.md"), self.root / ".mcp.json"])

    # ---- running -------------------------------------------------------------------------------------------------

    def run(
        self, goal: str, *, dry_run: bool = False, client: Any = None, model: str = "", approve: Any = None
    ) -> RunResult:
        """Run one command to the end, or until it needs a person, with the API runner. `dry_run` uses a scripted
        model: free, and not real work. Otherwise it uses the model `trypro.yaml` names, or the `client` and
        `model` given. A question for a person ends the call with status "blocked"; a person answers it with
        `trypro decide`, and `resume` continues it."""
        runner, orchestrator = self._runner(dry_run, client, model, approve)
        try:
            return runner.run(goal)
        finally:
            orchestrator.close()

    def resume(
        self, run_id: str, *, dry_run: bool = False, client: Any = None, model: str = "", approve: Any = None
    ) -> RunResult:
        runner, orchestrator = self._runner(dry_run, client, model, approve)
        try:
            return runner.resume(run_id)
        finally:
            orchestrator.close()

    def runs(self) -> list[str]:
        """Every run's id, newest first."""
        orchestrator = self._orchestrator()
        try:
            listed = orchestrator.ledger.list_runs()
        finally:
            orchestrator.close()
        return [run_id for run_id, _ in sorted(listed, key=lambda row: row[1], reverse=True)]

    def status(self, run_id: str) -> RunStatus:
        orchestrator = self._orchestrator()
        try:
            state = orchestrator.state(run_id)
            return RunStatus(
                run_id=run_id,
                goal=state.goal,
                phase=state.phase.value,
                questions=[request.question for request in state.human_requests.values()],
                input_tokens=state.usage.input_tokens,
                output_tokens=state.usage.output_tokens,
            )
        finally:
            orchestrator.close()

    def deliverables(self, run_id: str) -> Path | None:
        """The folder a person reads: the approved work, or its drafts, labelled, if the run stopped."""
        folder = self.root / "deliverables" / run_id
        if (folder / "index.md").is_file():
            return folder
        if (folder / "draft" / "index.md").is_file():
            return folder / "draft"
        return None

    def efficiency(self, run_id: str) -> dict[str, Any]:
        """How a run spent its time: minutes of work and of waiting for a person, model calls and tokens by step and
        by role, and the extra rounds: rework, refused hand-ins, escalations, handoffs. From its audit log alone."""
        from .core.efficiency import measure

        orchestrator = self._orchestrator()
        try:
            return measure(orchestrator.audit_path(run_id), run_id).to_dict()
        finally:
            orchestrator.close()

    def final_report(self, run_id: str) -> dict[str, Any] | None:
        """The final report's content, once a run is done."""
        orchestrator = self._orchestrator()
        try:
            state = orchestrator.state(run_id)
            final = state.artifacts.get(state.final_artifact_id or "")
            return dict(final.payload) if final is not None and state.phase.value == "done" else None
        finally:
            orchestrator.close()

    # ---- internals -----------------------------------------------------------------------------------------------

    def _orchestrator(self) -> Orchestrator:
        config = load_config(self.root)
        return Orchestrator(
            load_org(config.org_dir),
            config.home,
            inputs_dir=self.root / "inputs",
            deliverables_dir=self.root / "deliverables",
        )

    def _runner(self, dry_run: bool, client: Any, model: str, approve: Any = None) -> tuple[ApiRunner, Orchestrator]:
        computer = load_config(self.root).raw.get("computer_tools", True) is not False
        if dry_run or client is not None:
            orchestrator = self._orchestrator()
            routes = ModelRoutes({"standard": model or "scripted", "executive": model or "scripted",
                                  "fast": model or "scripted"})  # fmt: skip
            runner = ApiRunner(
                orchestrator,
                client or ScriptedClient(),
                routes,
                computer=computer,
                approve=approve,
                hold=approve is None,
            )
            return runner, orchestrator
        import argparse
        import os

        from .cli import _runner

        previous = Path.cwd()
        os.chdir(self.root)
        try:
            runner, orchestrator = _runner(argparse.Namespace(dry_run=False))
        finally:
            os.chdir(previous)
        runner.approve = approve
        # With nobody to ask, a command that changes something outside waits for a person, as `trypro held` shows.
        runner.hold = approve is None
        return runner, orchestrator
