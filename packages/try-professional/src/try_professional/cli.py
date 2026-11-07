"""The trypro command."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from skill_customs import Lockfile, SkillCustomsError, discover, verify

from . import __version__
from .adapters.api_runner.clients import ClientError
from .adapters.api_runner.runner import ApiRunner, ModelRoutes, RunResult
from .adapters.api_runner.scripted import ScriptedClient
from .adapters.mcp import McpService, ToolError
from .adapters.mcp.claude_code import write_claude_code_files
from .check_model import NOT_RECOMMENDED, ModelUnreachable, ScriptedModel, check_model, render_check
from .config import (
    CONFIG_NAME,
    CONFIG_TEMPLATE,
    ClientRouter,
    ModelConfig,
    ProjectConfig,
    build_client,
    build_search,
    load_config,
    load_env_file,
)
from .core.audit import read_records
from .core.brief import build_brief, role_card
from .core.lock import command_lock
from .core.orchestrator import Orchestrator, utc_now
from .core.pipeline import HumanDecided, IllegalEvent, Purpose
from .generator import (
    ActivationError,
    ArchitectFailed,
    Proposal,
    ProposalError,
    activate,
    apply_edit,
    create_proposal,
    load_proposal,
    load_template,
    pull,
    rank_templates,
    regenerate,
    render,
    template_ids,
)
from .memory import (
    FastEmbedEmbedder,
    MemoryConfigError,
    MemoryGateway,
    MemorySettings,
    MnemosyneBackend,
    bank_size,
    check_local_only,
    model_cache_dir,
    score_floor,
    tier_sizes,
)
from .org import Org, OrgValidationError, load_org, validate_bundle
from .org.loader import read_documents
from .org.policy import check_policy
from .packs import add_pack, available_packs, install_org, remove_pack
from .resources import COMMAND_PEAK_MB, LowMemory, wait_for_memory
from .skills import (
    DEFAULT_UNUSED_RUNS,
    UNCATEGORIZED,
    Decisions,
    LoadoutStore,
    LockedSkills,
    ProposalWriter,
    RerankCache,
    SkillCatalog,
    match_all,
    rerank,
    shortlist,
    sync_catalog,
)


def _print(text: str = "") -> None:
    """Print any language, even on a console whose encoding can't show it."""
    stream = sys.stdout
    encoding = stream.encoding or "utf-8"
    stream.write(text.encode(encoding, errors="backslashreplace").decode(encoding) + "\n")


def _write_project_files(root: Path) -> None:
    config = root / CONFIG_NAME
    if not config.exists():
        config.write_text(CONFIG_TEMPLATE, encoding="utf-8", newline="\n")
    home = root / ".trypro"
    home.mkdir(exist_ok=True)
    (home / ".gitignore").write_text("*\n", encoding="utf-8", newline="\n")


def _model_for_design(config: ProjectConfig) -> tuple[Any, str] | None:
    """The default model, or None if none is set up. Calling this spends nothing."""
    try:
        return build_client(config.model), config.model.model_for_tier("standard")
    except ClientError:
        return None


def _editor() -> list[str]:
    choice = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    return choice.split() if choice else (["notepad"] if os.name == "nt" else ["nano"])


def _seed_company_memory(bundle: Proposal, root: Path, by: str) -> None:
    """A person just approved this company, so its description is the first thing it remembers."""
    description = str(bundle.meta.get("description") or "")
    if not description:
        return
    try:
        config = load_config(root)
        org = load_org(config.org_dir)
        memory = _memory(config, org)
        if memory is not None:
            memory.promote(org.root_role, f"What this company is: {description}", f"approved at init by {by}")
    except Exception as error:
        _print(f"Couldn't save the company's description to its memory ({type(error).__name__}). Nothing else changed.")


def _person_at_terminal() -> bool:
    if not sys.stdin.isatty():
        return False
    if sys.platform == "win32":
        # isatty() is also true for an empty stdin here, and a prompt would then wait forever.
        import ctypes
        import msvcrt

        try:
            handle = msvcrt.get_osfhandle(sys.stdin.fileno())
        except OSError:
            return True  # Not a real file, so there's no console to ask. Its isatty() answer stands.
        mode = ctypes.c_ulong()
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    return True


def _review(bundle: Proposal, root: Path, embedder: Any, *, by: str, client: Any, model: str, discovery: bool) -> int:
    """The one review screen. Nothing activates unless a person types the confirmation here."""
    while True:
        _print(render(bundle))
        _print()
        if not _person_at_terminal():
            _print(f"Approving needs a person at a terminal. Nothing was activated. Proposal: {bundle.path}")
            _print(f"To review it: trypro init --review {bundle.id}")
            return 3
        choice = input("[a]pprove  [e]dit  [r]egenerate  [q]uit: ").strip().lower()
        if choice.startswith("q"):
            _print(f"Nothing was activated. To come back to it: trypro init --review {bundle.id}")
            return 0
        if choice.startswith("a"):
            if input("Type 'approve' to activate this company: ").strip() != "approve":
                _print("It didn't match, so nothing changed.")
                continue
            try:
                activate(bundle, root, embedder, by=by)
            except ActivationError as error:
                _print(f"Not activated: {error}")
                return 1
            _seed_company_memory(bundle, root, by)
            _print("Approved. Files go to deliverables/. Put anything roles should read in inputs/.")
            _print('Next: trypro run "<your goal>"')
            return 0
        if choice.startswith("e"):
            subprocess.run([*_editor(), str(bundle.path / "proposal.yaml")], check=False)
            try:
                bundle = apply_edit(bundle, root, embedder, client=client, model=model, say=_print)
            except (ProposalError, ValueError) as error:
                _print(f"That edit can't be used, so it wasn't applied: {error}")
            continue
        if choice.startswith("r"):
            if client is None:
                _print("Regenerating needs a model, and none is set up.")
                continue
            reason = input("What should change? ").strip()
            spent = bundle.architect_tokens
            _print(f"This calls the model again. {spent:,} design tokens spent so far.")
            try:
                bundle = regenerate(
                    bundle, root, embedder, reason, client=client, model=model, discovery=discovery, say=_print
                )
            except ArchitectFailed as error:
                _print(f"Couldn't regenerate, so the proposal is unchanged: {error}")


def _init_company(args: argparse.Namespace, root: Path) -> int:
    """trypro init "<business>", --template, or --review. Ends at the review screen."""
    if (root / "org" / "org.yaml").exists() and not args.review:
        _print(f"{root} already has a company in org/. Move it first, or use another --dir.")
        return 1
    _write_project_files(root)
    config = load_config(root)
    embedder = FastEmbedEmbedder(config.memory.embedding_model)
    discovery = not args.no_discover
    client: Any = None
    model = ""
    if not args.template or args.review:
        found = None if args.dry_run else _model_for_design(config)
        if found is not None:
            client, model = found
    by = args.by or getpass.getuser()
    if args.review:
        bundle = load_proposal(root, args.review)
        return _review(bundle, root, embedder, by=by, client=client, model=model, discovery=discovery)
    if args.description and client is None and not args.dry_run:
        closest, score = rank_templates(args.description, embedder)[0]
        _print("No model is set up, so this company can't be designed. Nothing was spent or changed.")
        _print(f"Closest template: {closest} (match {score:.2f}). To use it unchanged, with no key:")
        _print(f"  trypro init --template {closest}")
        _print("To design one, set TRYPRO_LLM_BASE_URL, TRYPRO_LLM_API_KEY, and TRYPRO_LLM_MODEL in .env.")
        return 2
    try:
        bundle = create_proposal(
            root,
            embedder,
            description=args.description or "",
            template_id=args.template,
            company=args.company,
            client=client,
            model=model,
            discovery=discovery,
            say=_print,
        )
    except ArchitectFailed as error:
        _print(f"Couldn't design this company, and nothing was activated: {error}")
        return 1
    return _review(bundle, root, embedder, by=by, client=client, model=model, discovery=discovery)


def cmd_templates(args: argparse.Namespace) -> int:
    for template_id in template_ids():
        template = load_template(template_id)
        _print(f"{template_id}: {template.description}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if args.description or args.template or args.review:
        if args.packs:
            _print("--packs builds the default company, so it can't be combined with a description or template.")
            return 2
        return _init_company(args, root)
    config = root / CONFIG_NAME
    org_dir = root / "org"
    if (config.exists() or (org_dir / "org.yaml").exists()) and not args.force:
        _print(f"{root} already has a company. Use --force to replace org/ and {CONFIG_NAME}.")
        return 1
    packs = args.packs.split(",") if args.packs else available_packs()
    unknown = sorted(set(packs) - set(available_packs()))
    if unknown:
        _print(f"Unknown packs: {', '.join(unknown)}. Available: {', '.join(available_packs())}")
        return 2
    install_org(org_dir, args.company or root.name, packs)
    config.write_text(CONFIG_TEMPLATE, encoding="utf-8", newline="\n")
    home = root / ".trypro"
    home.mkdir(exist_ok=True)
    (home / ".gitignore").write_text("*\n", encoding="utf-8", newline="\n")
    org = load_org(org_dir)
    _print(f"Created a company with {len(org.departments)} departments and {len(org.roles)} roles in {root}")
    if not args.skip_model:
        _print(f"Downloading the local embedding model if needed (about 0.22 GB, once) to {model_cache_dir()}")
        FastEmbedEmbedder().embed_documents(["ready"])
    _print('Try it with no key or cost: trypro run --dry-run "Build a health check endpoint"')
    _print(f"To use a real model, edit {CONFIG_NAME} and set the API key variable it names.")
    return 0


def cmd_org_validate(args: argparse.Namespace) -> int:
    directory = Path(args.path) if args.path else load_config(Path.cwd()).org_dir
    org, roles = read_documents(directory)
    issues = validate_bundle(org, roles)
    if args.policy:
        issues += check_policy(org, roles)
    for issue in issues:
        _print(f"{issue.severity}: {issue}")
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        _print(f"Invalid: {len(errors)} error(s).")
        return 1
    _print(f"Valid: {len(org['departments'])} departments, {len(roles)} roles.")
    return 0


def cmd_org_show(args: argparse.Namespace) -> int:
    org = load_org(load_config(Path.cwd()).org_dir)
    _print(f"{org.company_name} (root: {org.root_role})")
    for department in org.departments.values():
        _print(f"  {department.id} [{department.activation}]")
        for role in department.roles:
            marker = " (head)" if role == department.head else ""
            _print(f"    {role}{marker}: {org.role(f'{department.id}/{role}').mission}")
    return 0


def cmd_pack(args: argparse.Namespace) -> int:
    if args.pack_command == "list":
        org_dir = load_config(Path.cwd()).org_dir
        installed = set(load_org(org_dir).departments)
        for name in available_packs():
            _print(f"{'*' if name in installed else ' '} {name}")
        return 0
    org_dir = load_config(Path.cwd()).org_dir
    try:
        if args.pack_command == "add":
            add_pack(org_dir, args.name)
        else:
            remove_pack(org_dir, args.name)
    except (KeyError, ValueError) as error:
        _print(str(error.args[0]))
        return 1
    org, roles = read_documents(org_dir)
    for issue in validate_bundle(org, roles):
        _print(f"{issue.severity}: {issue}")
    _print(f"{args.pack_command}: {args.name}")
    return 0


def _print_result(result: RunResult, deliverables: Path | None = None) -> int:
    _print(f"Run {result.run_id}: {result.status}")
    if deliverables is not None and (deliverables / "index.md").is_file():
        _print(f"Deliverables: {deliverables / 'index.md'}")
    total = result.input_tokens + result.output_tokens
    cached = f", {result.cached_input_tokens} of the input cached" if result.cached_input_tokens else ""
    if result.size:
        _print(f"Size: {result.size}")
    _print(
        f"Tokens: {total} total ({result.input_tokens} in, {result.output_tokens} out{cached}), {result.calls} calls"
    )
    if result.final:
        _print()
        _print(result.final["summary_in_user_language"])
        _print()
        for item in result.final["criteria_results"]:
            _print(f"  [{'x' if item['met'] else ' '}] {item['criterion']}")
        for limit in result.final.get("known_limits", []):
            _print(f"  Limit: {limit}")
    if result.reason:
        _print(f"Reason: {result.reason}")
    for question in result.questions:
        _print(f"Needs a decision: {question.question}")
        options = "|".join(question.options)
        _print(f"  trypro decide {result.run_id} {question.request_id} <{options}>")
    return {"done": 0, "blocked": 3}.get(result.status, 1)


def _runner(args: argparse.Namespace) -> tuple[ApiRunner, Orchestrator]:
    config = load_config(Path.cwd())
    org = load_org(config.org_dir)
    inputs, deliverables = config.root / "inputs", config.root / "deliverables"
    if args.dry_run:
        orchestrator = Orchestrator(org, config.home, inputs_dir=inputs, deliverables_dir=deliverables)
        _print("Dry run: a scripted model is standing in. No model is called, and nothing here reflects real work.")
        return ApiRunner(orchestrator, ScriptedClient(), ModelRoutes({"standard": "scripted"})), orchestrator
    router = ClientRouter(config.model, org)
    # Fail before a run exists if the default key or endpoint is missing, or memory isn't safe.
    router.route(org.role(org.root_role))
    orchestrator = Orchestrator(
        org,
        config.home,
        memory=_memory(config, org),
        skills=_skills(config, org),
        proposals=ProposalWriter(config.root / "skills" / "proposed"),
        search=build_search(config.search),
        inputs_dir=inputs,
        deliverables_dir=deliverables,
    )
    if isinstance(orchestrator.skills, LockedSkills):
        orchestrator.feedback = orchestrator.skills
    return ApiRunner(orchestrator, router=router), orchestrator


def _memory_settings(config: ProjectConfig) -> MemorySettings:
    return MemorySettings(
        data_dir=config.home / "memory",
        embedding_model=config.memory.embedding_model,
        remote_allowed=config.memory.remote_allowed,
    )


def _memory(config: ProjectConfig, org: Org) -> MemoryGateway | None:
    if not config.memory.enabled:
        return None
    return MemoryGateway(
        org, MnemosyneBackend(_memory_settings(config)), floor=score_floor(config.memory.embedding_model)
    )


def _catalog(config: ProjectConfig) -> SkillCatalog:
    return SkillCatalog(config.home / "skills" / "catalog.sqlite", FastEmbedEmbedder(config.memory.embedding_model))


def _store(config: ProjectConfig) -> LoadoutStore:
    return LoadoutStore(config.home / "skills" / "loadouts.sqlite")


def _decisions(config: ProjectConfig) -> Decisions:
    return Decisions(config.root / "skills" / "decisions.yaml")


def _skills(config: ProjectConfig, org: Org) -> LockedSkills | None:
    """Agents get skills only once a catalog exists. Until then they simply have none."""
    if not (config.home / "skills" / "catalog.sqlite").is_file():
        return None
    lock = Lockfile.load(config.root / "skills.lock")
    return LockedSkills(org, _catalog(config), config.root / "skills", lock, _store(config))


def _require_person(action: str) -> bool:
    """These decide what agents can use, so they won't run from a script or an agent."""
    if sys.stdin.isatty():
        return True
    _print(f"{action} needs a person at a terminal, so it won't run from a script or an agent.")
    return False


def _judge_skills(
    config: ProjectConfig, org: Org, catalog: SkillCatalog, decisions: Decisions
) -> dict[Any, Any] | None:
    """Have the model judge what changed since last time. Reuses earlier verdicts, and reports what it spent."""
    found = _model_for_design(config)
    if found is None:
        _print("No model is set up, so skills were matched by embeddings alone.")
        return None
    client, model = found
    lists = shortlist(org, catalog.eligible(), catalog.embedder, decisions)
    if not any(lists.values()):
        return None
    cache = RerankCache(config.home / "skills" / "rerank.sqlite")
    verdicts, report = rerank(org, lists, client, model, cache)
    cache.close()
    _print(
        f"Judged {report.judged} role and skill pairs and reused {report.reused}: "
        f"{report.total_tokens} tokens, {report.calls} calls."
    )
    for role in report.failed_roles:
        _print(f"  Couldn't judge skills for {role}, so it was left out this time.")
    for role in report.chose_none:
        _print(f"  The model chose no skills for {role}.")
    if report.calls:
        _print(f"Its raw answers are in {report.raw_log}.")
    return verdicts


def _sync_and_match(
    config: ProjectConfig, org: Org, *, judge: bool = False
) -> tuple[dict[str, Any], LoadoutStore] | None:
    lock = Lockfile.load(config.root / "skills.lock")
    problems = verify(lock, dest=config.root / "skills")
    if problems:
        for problem in problems:
            _print(f"  {problem}")
        _print("Not syncing: installed skills don't match skills.lock.")
        return None
    decisions = _decisions(config)
    catalog = _catalog(config)
    store = _store(config)
    count = sync_catalog(catalog, lock, config.root / "skills", decisions)
    trust = Counter(entry.trust for entry in lock.entries.values())
    _print(f"Catalog holds {count} skill(s): {dict(trust)}. Quarantined skills are never offered to agents.")
    verdicts = _judge_skills(config, org, catalog, decisions) if judge else None
    loadouts = match_all(org, catalog, store, decisions, verdicts)
    catalog.close()
    return loadouts, store


def cmd_skills_match(args: argparse.Namespace) -> int:
    config = load_config(Path.cwd())
    org = load_org(config.org_dir)
    result = _sync_and_match(config, org, judge=not args.no_rerank)
    if result is None:
        return 1
    loadouts, store = result
    active = sum(len(item.active) for item in loadouts.values())
    pending = sum(len(item.pending) for item in loadouts.values())
    _print(f"{len(loadouts)} roles: {active} skills approved and active, {pending} waiting for a person.")
    if pending:
        _print("Review with: trypro skills loadout <role>. Admit with: trypro skills admit <skill> --by <you>")
    for item in store.unused(args.unused_runs):
        _print(f"Consider removing: {item['skill_id']} from {item['role']}, unused for {item['idle_runs']} runs.")
    store.close()
    return 0


def cmd_skills_loadout(args: argparse.Namespace) -> int:
    config = load_config(Path.cwd())
    org = load_org(config.org_dir)
    if not org.has_role(args.role):
        _print(f"'{args.role}' isn't a role. Roles look like engineering/backend.")
        return 2
    store = _store(config)
    role = org.role(args.role)
    active = store.section(args.role, "active")
    _print(f"{args.role}: {len(active)} of at most {role.max_skills} skills")
    for title, section in (("Active", "active"), ("Waiting for a person", "pending"), ("Over the cap", "over_cap")):
        items = active if section == "active" else store.section(args.role, section)
        if items:
            _print(f"{title}:")
        for item in items:
            _print(f"  {item['rank']:>2}. {item['score']:.2f}  {item['skill_id']}")
            _print(f"      {item['why']}")
    for proposal in store.proposals(args.role):
        _print(f"Proposed by {proposal['proposed_by']}: {proposal['skill_id']}. {proposal['reason']}")
    if org.is_senior(args.role):
        catalog = _catalog(config)
        names = [r.skill_id for r, _ in catalog.eligible() if r.category == UNCATEGORIZED]
        catalog.close()
        if names:
            _print("Uncategorized, visible only to the CEO and department heads:")
            for name in names:
                _print(f"  [uncategorized] {name}")
    store.close()
    return 0


def cmd_skills_gaps(args: argparse.Namespace) -> int:
    config = load_config(Path.cwd())
    store = _store(config)
    gaps = store.gaps()
    store.close()
    if not gaps:
        _print("No skill gaps recorded.")
        return 0
    for gap in gaps:
        _print(f"{gap['count']:>3}x  {gap['role']}  best match {gap['best_score']:.2f}")
        _print(f"      {gap['description']}")
    if not args.discover:
        _print("To look for skills on GitHub: trypro skills gaps --discover. This sends those descriptions to GitHub.")
        return 0
    _print("Candidates from GitHub. Nothing was installed, and each still needs skill-customs and a person's approval:")
    for gap in gaps:
        _print(f"For: {gap['description'][:80]}")
        for candidate in discover(gap["description"][:200], top=5):
            _print(f"  {candidate.score:>6.2f}  {candidate.repo}  {candidate.license_spdx}")
    return 0


def cmd_skills_admit(args: argparse.Namespace) -> int:
    if not _require_person("admit"):
        return 2
    config = load_config(Path.cwd())
    org = load_org(config.org_dir)
    catalog = _catalog(config)
    record = catalog.record(args.skill_id)
    catalog.close()
    if record is None or record.trust == "quarantined":
        _print(f"'{args.skill_id}' doesn't exist, or is still quarantined. Approve it with skill-customs first.")
        return 1
    if record.category == UNCATEGORIZED:
        _print(
            f"'{args.skill_id}' has no category. Assign one first: trypro skills categorize {args.skill_id} <category>"
        )
        return 1
    store = _store(config)
    if args.role:
        roles = [args.role]
    else:
        roles = [ref for ref in org.roles if any(i["skill_id"] == args.skill_id for i in store.section(ref, "pending"))]
    store.close()
    if not roles:
        _print("No role is waiting for this skill. Use --role to name one.")
        return 1
    _print(f"Admit {args.skill_id} into: {', '.join(roles)}")
    if input("Type the skill ID to confirm: ").strip() != args.skill_id:
        _print("It didn't match, so nothing changed.")
        return 1
    decisions = _decisions(config)
    for role in roles:
        decisions.decide(role, args.skill_id, "approved", by=args.by, content_sha256=record.content_sha256)
    decisions.save()
    _print(f"Recorded in {decisions.path.name}. Run 'trypro skills match' to apply it.")
    return 0


def cmd_skills_reject(args: argparse.Namespace) -> int:
    if not _require_person("reject"):
        return 2
    config = load_config(Path.cwd())
    catalog = _catalog(config)
    record = catalog.record(args.skill_id)
    catalog.close()
    if record is None:
        _print(f"'{args.skill_id}' doesn't exist.")
        return 1
    decisions = _decisions(config)
    decisions.decide(args.role, args.skill_id, "rejected", by=args.by, content_sha256=record.content_sha256)
    decisions.save()
    _print(f"{args.skill_id} won't be offered to {args.role}. Run 'trypro skills match' to apply it.")
    return 0


def cmd_skills_categorize(args: argparse.Namespace) -> int:
    if not _require_person("categorize"):
        return 2
    config = load_config(Path.cwd())
    org = load_org(config.org_dir)
    known = {category for role in org.roles.values() for category in role.allowed_skill_categories}
    if args.category not in known:
        _print(f"'{args.category}' isn't a category any role uses. Choose from: {', '.join(sorted(known))}")
        return 1
    decisions = _decisions(config)
    decisions.assign_category(args.skill_id, args.category, by=args.by)
    decisions.save()
    _print(f"{args.skill_id} is now in {args.category}. Run 'trypro skills match' to apply it.")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    try:
        from .adapters.mcp.server import build_server
    except ImportError:
        print("The MCP server needs: pip install 'try-professional[mcp]'", file=sys.stderr)
        return 2
    root = Path(args.dir).resolve() if args.dir else Path.cwd()

    def company() -> McpService | None:
        try:
            config = load_config(root)
        except FileNotFoundError:
            return None
        org = load_org(config.org_dir)
        orchestrator = Orchestrator(
            org,
            config.home,
            memory=_memory(config, org),
            skills=_skills(config, org),
            proposals=ProposalWriter(config.root / "skills" / "proposed"),
            inputs_dir=config.root / "inputs",
            deliverables_dir=config.root / "deliverables",
            # An operator's client has its own tools and files, which this can't see.
            check_claimed_files=bool(args.role),
        )
        if isinstance(orchestrator.skills, LockedSkills):
            orchestrator.feedback = orchestrator.skills
        return McpService(orchestrator, bound_role=args.role)

    # A role can't design the company it works in.
    design = None if args.role else _Design(root)
    # stdout belongs to the protocol from here on, so nothing else may print to it.
    build_server(company, design).run("stdio")
    return 0


class _Design:
    """Designing a company over MCP. The embedding model loads only if this is used."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._embedder: Any = None

    @property
    def embedder(self) -> Any:
        if self._embedder is None:
            self._embedder = FastEmbedEmbedder()
        return self._embedder

    def _refused_as_tool_errors(self, call: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        load_env_file(self.root)
        try:
            result: dict[str, Any] = call(self.root, *args, **kwargs)
        except pull.PullError as error:
            raise ToolError(str(error)) from error
        return result

    def propose(self, description: str, company: str | None) -> dict[str, Any]:
        return self._refused_as_tool_errors(pull.begin_design, self.embedder, description, company=company)

    def get(self, proposal_id: str) -> dict[str, Any]:
        return self._refused_as_tool_errors(pull.proposal_status, proposal_id)

    def submit(self, proposal_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._refused_as_tool_errors(pull.submit_design, self.embedder, proposal_id, payload)

    def judge(self, proposal_id: str, role: str, decisions: list[Any]) -> dict[str, Any]:
        return self._refused_as_tool_errors(pull.submit_judgment, self.embedder, proposal_id, role, decisions)


def cmd_agents(args: argparse.Namespace) -> int:
    config = load_config(Path.cwd())
    org = load_org(config.org_dir)
    written = write_claude_code_files(config.root, org)
    _print(f"Wrote {len(org.roles)} role agents to .claude/agents, the /company command, and .mcp.json.")
    _print("Start Claude Code in this folder (restart it if it's open, so it loads them), and run:")
    _print('  /company "<what you want done>"')
    _print(f"That's {len(written)} files. Run this again whenever the company changes.")
    return 0


def _model_to_check(args: argparse.Namespace) -> tuple[Any, str]:
    if args.dry_run:
        return ScriptedModel(), "scripted (a stand-in, to show the command working)"
    try:
        config = load_config(Path.cwd()).model
    except FileNotFoundError:
        load_env_file(Path.cwd())
        config = ModelConfig(
            provider="openai_compatible",
            tiers={},
            api_key_env="TRYPRO_LLM_API_KEY",
            base_url=os.environ.get("TRYPRO_LLM_BASE_URL"),
            default_model=os.environ.get("TRYPRO_LLM_MODEL"),
        )
    return build_client(config), args.model or config.model_for_tier("standard")


def cmd_check_model(args: argparse.Namespace) -> int:
    """0 if it can run a company, 1 if it's not recommended, 2 if it isn't set up, and 3 if it can't be reached."""
    try:
        client, model = _model_to_check(args)
    except ClientError as error:
        _print(f"Can't check a model yet: {error}")
        return 2
    try:
        report = check_model(client, model)
    except ModelUnreachable as error:
        _print(f"Couldn't reach {model}, so there's no verdict. That says nothing about the model: {error}")
        return 3
    _print(json.dumps(report.to_dict(), ensure_ascii=False, indent=1) if args.json else render_check(report))
    return 1 if report.verdict == NOT_RECOMMENDED else 0


def _show_unfinished(orchestrator: Orchestrator) -> None:
    unfinished = orchestrator.company.unfinished()
    if unfinished:
        _print(
            "Unfinished from earlier commands. Continue with `trypro resume <id>`, or stop with `trypro cancel <id>`:"
        )
        for item in unfinished:
            _print(f"  {item['run_id']}  {item['status']:<8} {item['goal'][:70]}")


def _finish_command(orchestrator: Orchestrator) -> None:
    done = orchestrator.compact()
    if any(done.values()):
        _print(f"Tidied old records: {done['log_entries_summarized']} log entries, {done['runs_archived']} runs.")
    waiting = orchestrator.company.queued()
    if waiting:
        _print(f"{len(waiting)} queued. Run the next one with: trypro next")


def _run_goal(orchestrator: Orchestrator, runner: ApiRunner, goal: str) -> int:
    wait_for_memory(COMMAND_PEAK_MB, say=_print)
    result = runner.run(goal)
    code = _print_result(result, orchestrator.deliverables_path(result.run_id))
    _finish_command(orchestrator)
    return code


def cmd_run(args: argparse.Namespace) -> int:
    runner, orchestrator = _runner(args)
    try:
        _show_unfinished(orchestrator)
        if args.queue:
            number = orchestrator.company.add_queued(args.goal, utc_now())
            _print(f"Queued as #{number}. Nothing has run or been spent. Run it with: trypro next")
            return 0
        with command_lock(orchestrator.home) as held:
            if not held:
                number = orchestrator.company.add_queued(args.goal, utc_now())
                _print(f"Another command is running, so this one is queued as #{number}. Run it with: trypro next")
                return 0
            return _run_goal(orchestrator, runner, args.goal)
    finally:
        orchestrator.close()


def cmd_next(args: argparse.Namespace) -> int:
    runner, orchestrator = _runner(args)
    try:
        with command_lock(orchestrator.home) as held:
            if not held:
                _print("Another command is running. Nothing was started.")
                return 1
            item = orchestrator.company.take_queued()
            if item is None:
                _print("Nothing is queued.")
                return 0
            _print(f"Running #{item['id']}: {item['goal']}")
            return _run_goal(orchestrator, runner, item["goal"])
    finally:
        orchestrator.close()


def cmd_resume(args: argparse.Namespace) -> int:
    runner, orchestrator = _runner(args)
    try:
        with command_lock(orchestrator.home) as held:
            if not held:
                _print("Another command is running. Nothing was resumed.")
                return 1
            wait_for_memory(COMMAND_PEAK_MB, say=_print)
            return _print_result(runner.resume(args.run_id), orchestrator.deliverables_path(args.run_id))
    finally:
        orchestrator.close()


def cmd_cancel(args: argparse.Namespace) -> int:
    orchestrator = _orchestrator()
    try:
        if args.target.startswith("#") or args.target.isdigit():
            item = orchestrator.company.take_queued(int(args.target.lstrip("#")))
            _print(f"Removed #{item['id']}: {item['goal']}" if item else "That isn't in the queue.")
            return 0 if item else 1
        try:
            orchestrator.cancel(args.target)
        except KeyError:
            _print(f"There's no command {args.target}.")
            return 1
        except IllegalEvent as error:
            _print(str(error))
            return 1
        _print(f"Cancelled {args.target}. Its records stay, and nothing more will be spent on it.")
        return 0
    finally:
        orchestrator.close()


def cmd_memory_pending(args: argparse.Namespace) -> int:
    orchestrator = _orchestrator()
    try:
        company = orchestrator.company.pending_memory("company")
        team = orchestrator.company.pending_memory("department")
        if not company and not team:
            _print("No memory changes are waiting.")
        for item in company:
            _print(f"#{item.id} company, from {item.proposed_by} ({item.source_run}): {item.text}")
        for item in team:
            _print(f"#{item.id} team lesson for {item.owner}'s head, from {item.proposed_by}: {item.text}")
        if company:
            _print("Decide company memory with: trypro memory review --by <your name>")
        return 0
    finally:
        orchestrator.close()


def cmd_memory_review(args: argparse.Namespace) -> int:
    """The CEO proposes company memory, and a person decides each item here."""
    if not _require_person("Changing company memory"):
        return 3
    config = load_config(Path.cwd())
    org = load_org(config.org_dir)
    memory = _memory(config, org)
    orchestrator = Orchestrator(org, config.home)
    try:
        for item in orchestrator.company.pending_memory("company"):
            _print(f"#{item.id} from {item.proposed_by} ({item.source_run}):\n  {item.text}")
            choice = input("[a]pprove  [r]eject  [s]kip: ").strip().lower()
            if choice.startswith("a"):
                if memory is None:
                    _print("Memory is turned off, so this can't be saved. It stays pending.")
                    continue
                memory.promote(org.root_role, item.text, f"{item.source}, approved by {args.by}")
                orchestrator.company.decide_memory(item.id, True, by=args.by, at=utc_now())
            elif choice.startswith("r"):
                orchestrator.company.decide_memory(item.id, False, by=args.by, at=utc_now())
        return 0
    finally:
        orchestrator.close()


def cmd_compact(args: argparse.Namespace) -> int:
    orchestrator = _orchestrator()
    try:
        done = orchestrator.compact(args.older_than_days)
        _print(f"Summarized {done['log_entries_summarized']} log entries, and archived {done['runs_archived']} runs.")
        _print("Raw logs stay in .trypro/runs/, compressed. trypro audit still verifies them.")
        return 0
    finally:
        orchestrator.close()


def _orchestrator() -> Orchestrator:
    config = load_config(Path.cwd())
    return Orchestrator(load_org(config.org_dir), config.home)


def cmd_decide(args: argparse.Namespace) -> int:
    orchestrator = _orchestrator()
    try:
        orchestrator.handle(
            args.run_id,
            HumanDecided(
                request_id=args.request_id,
                decision=args.decision,
                note=args.note or "",
                new_token_budget=args.token_budget,
            ),
        )
    except IllegalEvent as error:
        _print(str(error))
        return 1
    finally:
        orchestrator.close()
    _print(f"Recorded. Continue with: trypro resume {args.run_id}")
    return 0


def _status_backlog(orchestrator: Orchestrator) -> None:
    _print("Backlog:")
    queued = orchestrator.company.queued()
    unfinished = orchestrator.company.unfinished()
    if not queued and not unfinished:
        _print("  Nothing waiting.")
    for item in queued:
        _print(f"  #{item['id']}  queued   {item['goal'][:70]}")
    for item in unfinished:
        _print(f"  {item['run_id']}  {item['status']:<8} {item['goal'][:70]}")


def _status_handoffs(orchestrator: Orchestrator) -> None:
    _print("Open handoffs:")
    handoffs = orchestrator.company.open_handoffs()
    if not handoffs:
        _print("  None.")
    for item in handoffs:
        _print(f"  {item['from'] or '?'} -> {item['to']}: {item['request'][:70]} ({item['run_id']})")


def _status_memory(orchestrator: Orchestrator, config: ProjectConfig, org: Org) -> None:
    _print("Memory by tier:")
    if not config.memory.enabled:
        _print("  Memory is turned off.")
    else:
        sizes = tier_sizes(org, lambda bank: bank_size(_memory_settings(config).data_dir, bank))
        for tier, size in sizes.items():
            _print(f"  {tier:<11} {size['entries']:>6} entries  {size['bytes'] / 1024:>9.1f} KB")
    company = len(orchestrator.company.pending_memory("company"))
    team = len(orchestrator.company.pending_memory("department"))
    _print(f"  Waiting for a decision: {company} company changes (yours), {team} team lessons (their heads').")


def _status_skill_gaps(config: ProjectConfig) -> None:
    if not (config.home / "skills" / "loadouts.sqlite").is_file():
        return
    store = _store(config)
    gaps = store.gaps()
    store.close()
    if gaps:
        _print(f"Skill gaps: {len(gaps)} needs with no skill. See them with: trypro skills gaps")


def _status_tokens(orchestrator: Orchestrator) -> None:
    _print("Tokens by month:")
    months = orchestrator.company.tokens_by_month()
    if not months:
        _print("  Nothing yet.")
    for row in months:
        _print(
            f"  {row['month']}: {row['commands']} commands, {row['input']} input ({row['cached_input']} cached), "
            f"{row['output']} output, {row['calls']} calls"
        )
    _print("Latest commands:")
    for row in orchestrator.company.tokens_by_command(limit=10):
        _print(
            f"  {row['run_id']}  {row['size']:<5} {row['status']:<9} {row['input']} in ({row['cached_input']} cached), "
            f"{row['output']} out  {row['goal'][:40]}"
        )


def _status_role(orchestrator: Orchestrator, org: Org, ref: str) -> int:
    if not org.has_role(ref):
        _print(f"There's no role {ref}.")
        return 1
    _print(role_card(org, ref))
    brief = build_brief(org, orchestrator.company, ref, Purpose.DISPATCH if org.is_head(ref) else Purpose.EXECUTE)
    _print(brief.state or "No work on record yet.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config(Path.cwd())
    org = load_org(config.org_dir)
    orchestrator = Orchestrator(org, config.home)
    try:
        if args.role:
            return _status_role(orchestrator, org, args.role)
        if not args.run_id:
            chosen = args.backlog or args.handoffs or args.memory or args.tokens
            if not chosen or args.backlog:
                _status_backlog(orchestrator)
            if not chosen or args.handoffs:
                _status_handoffs(orchestrator)
            if not chosen or args.memory:
                _status_memory(orchestrator, config, org)
                _status_skill_gaps(config)
            if not chosen or args.tokens:
                _status_tokens(orchestrator)
            return 0
        state = orchestrator.state(args.run_id)
        _print(f"Run {state.run_id}: {state.phase.value}")
        _print(f"Goal: {state.goal}")
        _print(f"Active departments: {', '.join(state.activated)}")
        for task in state.tasks.values():
            _print(f"  {task.task_id}: {task.status.value} (owner {task.owner_role})")
        _print(f"Tokens: {state.usage.total} total, {state.usage.calls} calls")
        for role, usage in sorted(state.usage_by_role.items(), key=lambda item: -item[1].total):
            _print(f"  {role}: {usage.total} tokens, {usage.calls} calls")
        _print(f"Size: {state.size}")
        for purpose, usage in sorted(state.usage_by_purpose.items(), key=lambda item: -item[1].total):
            _print(f"  {purpose}: {usage.total} tokens, {usage.calls} calls")
        _print(f"Counters: {state.counters.model_dump()}")
        return 0
    finally:
        orchestrator.close()


def cmd_audit(args: argparse.Namespace) -> int:
    orchestrator = _orchestrator()
    try:
        result = orchestrator.verify_audit(args.run_id)
        if args.show and result.records:
            for record in read_records(orchestrator.audit_path(args.run_id)):
                _print(json.dumps({k: record[k] for k in ("seq", "ts", "kind", "data")}, ensure_ascii=False))
        if result.ok:
            _print(f"Audit log OK: {result.records} records.")
            return 0
        _print(f"Audit log FAILED: {result.problem}")
        return 1
    finally:
        orchestrator.close()


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config(Path.cwd())
    ok = True
    org = load_org(config.org_dir)
    _print(f"[ok] org: {len(org.departments)} departments, {len(org.roles)} roles")
    _print(f"[ok] python {sys.version.split()[0]}")
    provider = config.model.provider
    _print(f"[--] model provider: {provider}")
    env = config.model.api_key_env
    if env:
        present = bool(os.environ.get(env))
        ok = ok and present
        _print(f"[{'ok' if present else '!!'}] {env} is {'set' if present else 'not set'}")
    if provider == "anthropic":
        try:
            import anthropic  # noqa: F401

            _print("[ok] anthropic SDK installed")
        except ImportError:
            ok = False
            _print("[!!] anthropic SDK missing: pip install 'try-professional[anthropic]'")
    if provider == "openai_compatible" and not config.model.base_url:
        ok = False
        _print("[!!] no model endpoint: set TRYPRO_LLM_BASE_URL")
    router = ClientRouter(config.model, org)
    try:
        for ref, role in org.roles.items():
            if org.llm_override_for(ref):
                resolved = router.resolve(role)
                variable = resolved.api_key_env
                state = "no key" if variable is None else ("set" if os.environ.get(variable) else "NOT SET")
                _print(f"[--] {ref}: model {resolved.model}, key variable {variable} ({state})")
        _print(f"[ok] default model: {config.model.model_for_tier('standard')}")
    except ClientError as error:
        ok = False
        _print(f"[!!] {error}")
    if config.memory.enabled:
        try:
            settings = _memory_settings(config)
            check_local_only(settings, os.environ)
            _print(
                f"[ok] memory: {settings.embedding_model}, local only"
                if not settings.remote_allowed
                else "[--] memory: remote allowed"
            )
        except MemoryConfigError as error:
            ok = False
            _print(f"[!!] {error}")
    else:
        _print("[--] memory is turned off")
    _print("Ready." if ok else "Not ready for real model runs. 'trypro run --dry-run' still works.")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trypro", description="A structured company of LLM agents.")
    parser.add_argument("--version", action="version", version=f"trypro {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Design a company for a business, or create the default one")
    init.add_argument("description", nargs="?", help='The business, such as "a YouTube Shorts content company"')
    init.add_argument("--template", help="Use a template unchanged, with no model. See: trypro templates")
    init.add_argument("--review", metavar="PROPOSAL", help="Go back to a proposal's review screen")
    init.add_argument("--no-discover", action="store_true", help="Don't search GitHub for skills")
    init.add_argument("--dry-run", action="store_true", help="Use the closest template unchanged: no key, no cost")
    init.add_argument("--by", help="Your name, for the record of what you approved. Default: your username")
    init.add_argument("--dir", default=".")
    init.add_argument("--company")
    init.add_argument("--packs", help="Comma-separated packs. Default: all")
    init.add_argument("--force", action="store_true")
    init.add_argument("--skip-model", action="store_true", help="Don't download the embedding model now")
    init.set_defaults(func=cmd_init)

    org = sub.add_parser("org", help="Inspect the organization").add_subparsers(dest="org_command", required=True)
    validate = org.add_parser("validate", help="Validate against spec org/v1")
    validate.add_argument("path", nargs="?")
    validate.add_argument("--policy", action="store_true", help="Also apply the rules for generated companies")
    validate.set_defaults(func=cmd_org_validate)
    org.add_parser("show", help="Print the org chart").set_defaults(func=cmd_org_show)

    sub.add_parser("templates", help="List industry templates").set_defaults(func=cmd_templates)

    pack = sub.add_parser("pack", help="Manage department packs").add_subparsers(dest="pack_command", required=True)
    pack.add_parser("list").set_defaults(func=cmd_pack)
    for name in ("add", "remove"):
        item = pack.add_parser(name)
        item.add_argument("name")
        item.set_defaults(func=cmd_pack)

    run = sub.add_parser("run", help="Run a goal")
    run.add_argument("goal")
    run.add_argument("--dry-run", action="store_true", help="Use a scripted model: no key, no cost")
    run.add_argument("--queue", action="store_true", help="Add it to the backlog without running it")
    run.set_defaults(func=cmd_run)

    next_command = sub.add_parser("next", help="Run the oldest queued command")
    next_command.add_argument("--dry-run", action="store_true")
    next_command.set_defaults(func=cmd_next)

    cancel = sub.add_parser("cancel", help="Stop an unfinished command, or remove a queued one")
    cancel.add_argument("target", help="A run ID, or a queue number such as 3")
    cancel.set_defaults(func=cmd_cancel)

    memory = sub.add_parser("memory", help="Company memory").add_subparsers(dest="memory_command", required=True)
    pending = memory.add_parser("pending", help="Show memory changes waiting for a decision")
    pending.set_defaults(func=cmd_memory_pending)
    memory_review = memory.add_parser("review", help="Decide the CEO's proposed company memory, at a terminal")
    memory_review.add_argument("--by", required=True)
    memory_review.set_defaults(func=cmd_memory_review)

    compact = sub.add_parser("compact", help="Summarize old work logs and archive old raw logs. Uses no model")
    compact.add_argument("--older-than-days", type=int, default=90)
    compact.set_defaults(func=cmd_compact)

    resume = sub.add_parser("resume", help="Continue a blocked or interrupted run")
    resume.add_argument("run_id")
    resume.add_argument("--dry-run", action="store_true")
    resume.set_defaults(func=cmd_resume)

    decide = sub.add_parser("decide", help="Answer a question a run is blocked on")
    decide.add_argument("run_id")
    decide.add_argument("request_id")
    decide.add_argument("decision")
    decide.add_argument("--note")
    decide.add_argument("--token-budget", type=int)
    decide.set_defaults(func=cmd_decide)

    status = sub.add_parser("status", help="Show the backlog, handoffs, memory and tokens, or one run")
    status.add_argument("run_id", nargs="?")
    status.add_argument("--backlog", action="store_true")
    status.add_argument("--handoffs", action="store_true")
    status.add_argument("--memory", action="store_true")
    status.add_argument("--tokens", action="store_true")
    status.add_argument("--role", help="One role's card and work state")
    status.set_defaults(func=cmd_status)

    audit = sub.add_parser("audit", help="Verify a run's audit log")
    audit.add_argument("run_id")
    audit.add_argument("--show", action="store_true")
    audit.set_defaults(func=cmd_audit)

    skills = sub.add_parser("skills", help="Agent skills").add_subparsers(dest="skills_command", required=True)
    match = skills.add_parser("match", help="Rebuild the catalog from skills.lock and recompute every loadout")
    match.add_argument("--no-rerank", action="store_true", help="Use embeddings alone: no model call, no cost")
    match.add_argument("--unused-runs", type=int, default=DEFAULT_UNUSED_RUNS, help="Flag skills unused this long")
    match.set_defaults(func=cmd_skills_match)
    loadout = skills.add_parser("loadout", help="Show a role's skills, with scores and why they matched")
    loadout.add_argument("role")
    loadout.set_defaults(func=cmd_skills_loadout)
    gaps = skills.add_parser("gaps", help="Tasks where a role had no skill that fit")
    gaps.add_argument("--discover", action="store_true", help="Search GitHub for these. Sends the descriptions there")
    gaps.set_defaults(func=cmd_skills_gaps)
    admit = skills.add_parser("admit", help="Let a skill into loadouts, as a person at a terminal")
    admit.add_argument("skill_id")
    admit.add_argument("--by", required=True)
    admit.add_argument("--role", help="Default: every role waiting for it")
    admit.set_defaults(func=cmd_skills_admit)
    reject = skills.add_parser("reject", help="Keep a skill out of a role's loadout")
    reject.add_argument("skill_id")
    reject.add_argument("--role", required=True)
    reject.add_argument("--by", required=True)
    reject.set_defaults(func=cmd_skills_reject)
    categorize = skills.add_parser("categorize", help="Give an uncategorized skill a category")
    categorize.add_argument("skill_id")
    categorize.add_argument("category")
    categorize.add_argument("--by", required=True)
    categorize.set_defaults(func=cmd_skills_categorize)

    mcp = sub.add_parser("mcp", help="Serve this company to an MCP client over stdio")
    mcp.add_argument("--role", help="Act as one role, with its identity fixed. Default: operator mode")
    mcp.add_argument("--dir", help="The company's folder. Default: the current folder")
    mcp.set_defaults(func=cmd_mcp)

    sub.add_parser("doctor", help="Check this project's setup").set_defaults(func=cmd_doctor)

    agents = sub.add_parser("agents", help="Set up an agent runtime to run this company")
    agents.add_argument("runtime", choices=["claude-code"])
    agents.set_defaults(func=cmd_agents)

    check = sub.add_parser("check-model", help="Test whether a model suits running a company. A few thousand tokens")
    check.add_argument("--model", help="The model to test. Default: the standard tier, or TRYPRO_LLM_MODEL")
    check.add_argument("--dry-run", action="store_true", help="Show it working on a scripted model, at no cost")
    check.add_argument("--json", action="store_true", help="Print the result as JSON")
    check.set_defaults(func=cmd_check_model)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result: int = args.func(args)
        return result
    except LowMemory as error:
        _print(f"Stopped at a safe point. {error}")
        _print("All work so far is saved. Free some memory, then continue with: trypro resume <run-id>")
        _print("trypro status lists unfinished work.")
        return 3
    except (
        FileNotFoundError,
        KeyError,
        OrgValidationError,
        ClientError,
        MemoryConfigError,
        SkillCustomsError,
        ActivationError,
    ) as error:
        _print(f"Error: {error.args[0] if error.args else error}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
