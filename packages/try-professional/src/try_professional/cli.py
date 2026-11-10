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
from . import setup as _setup
from .adapters.api_runner.clients import ClientError
from .adapters.api_runner.runner import ApiRunner, ModelRoutes, RunResult
from .adapters.api_runner.scripted import ScriptedClient
from .adapters.mcp import McpService, ToolError
from .adapters.mcp.claude_code import refresh_claude_code_files, write_claude_code_files
from .adapters.mcp.service import MCP_ESTIMATE_FACTOR
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
from .core.company import DROPPED_BY
from .core.lock import command_lock
from .core.orchestrator import Orchestrator, utc_now
from .core.outside import OUTCOME_STATUSES
from .core.pipeline import HumanDecided, IllegalEvent, Purpose
from .doctor import print_checks, report, run_checks
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
from .generator.provision import GITHUB_TOKEN_HELP, github_token_is_set
from .memory import (
    FastEmbedEmbedder,
    MemoryConfigError,
    MemoryGateway,
    MemorySettings,
    MnemosyneBackend,
    bank_size,
    model_cache_dir,
    score_floor,
    tier_sizes,
)
from .memory.embedding import use_embedding_service
from .org import Org, OrgValidationError, load_org, validate_bundle
from .org.loader import read_documents
from .org.policy import check_policy
from .packs import add_pack, available_packs, default_packs, install_org, remove_pack
from .resources import (
    COMMAND_PEAK_MB,
    GENERATION_AFTER_MODEL_LOADED_MB,
    GENERATION_PEAK_MB,
    LowMemory,
    memory_shortfall,
    wait_for_memory,
)
from .setup import CLAUDE_CODE, runtime_of, setup_claude_code
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
            if runtime_of(root) == CLAUDE_CODE:
                # trypro setup claude-code was run here, so the roles are written now, not as a separate step.
                written = write_claude_code_files(root, load_org(root / "org"))
                _print(f"Wrote {len(written)} files for Claude Code: a subagent per role, and the /company command.")
                _print("Next: start Claude Code here again, and run: /company <a command>")
            else:
                _print("Next, in Claude Code: trypro agents claude-code, then /company <a command>")
                _print('Or, with the experimental API runner: trypro run "<your goal>"')
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
    load_env_file(root)
    if not args.review and not args.no_discover and not args.dry_run and not github_token_is_set():
        # Skills are optional, so a missing token never stops the first run: its question was the first thing a new
        # person had to answer, and the default was to stop. Companies designed with no skills do their work.
        _print(GITHUB_TOKEN_HELP)
        _print("Going on with no skills: its roles work from their written instructions.")
        _print()
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
    packs = args.packs.split(",") if args.packs else default_packs()
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
    if not github_token_is_set():
        _print("This default company has no skills. To design one with skills, set GITHUB_TOKEN. See: trypro doctor")
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


def _org_change_detail(proposal: Any) -> list[str]:
    change = proposal.change
    lines = [proposal.line(), f"   Proposed in {proposal.source_run} by {proposal.proposed_by}."]
    if change.get("responsibilities"):
        lines.append("   Does: " + "; ".join(change["responsibilities"]))
    if change.get("skill_needs"):
        lines.append("   Skills it needs: " + "; ".join(change["skill_needs"]))
    return lines


def cmd_org_proposals(args: argparse.Namespace) -> int:
    from .api import Company

    proposals = Company(Path.cwd()).org_proposals(None if args.all else "pending")
    if not proposals:
        _print("No changes to the company are waiting." if not args.all else "The CEO hasn't proposed any changes.")
        return 0
    for proposal in proposals:
        for line in _org_change_detail(proposal):
            _print(line)
        if args.all:
            _print(f"   Status: {proposal.status}")
    if not args.all:
        _print("To decide them: trypro org review")
    return 0


def _decide_org_change(company: Any, proposal_id: int, accept: bool, by: str) -> int:
    from .api import CompanyError

    try:
        added = company.decide_org_change(proposal_id, accept, by=by)
    except CompanyError as error:
        _print(str(error))
        return 1
    if added is None:
        _print(f"#{proposal_id} rejected. The CEO won't propose it again.")
    else:
        _print(f"#{proposal_id} added: {added.ref}, {added.title}. It works from the next command.")
        if (company.root / ".claude" / "agents").is_dir():
            _print("Claude Code's files were updated. Restart Claude Code in this folder so it sees the new role.")
    return 0


def cmd_org_review(args: argparse.Namespace) -> int:
    """Only a person changes who works here, as only a person approves the company at first."""
    from .api import Company

    company = Company(Path.cwd())
    proposals = company.org_proposals("pending")
    if not proposals:
        _print("No changes to the company are waiting.")
        return 0
    if not _person_at_terminal():
        _print("Deciding changes to the company needs a person at a terminal. Nothing was changed.")
        return 3
    by = args.by or getpass.getuser()
    for proposal in proposals:
        for line in _org_change_detail(proposal):
            _print(line)
        choice = input("[a]dd it  [r]eject it  [s]kip for now: ").strip().lower()
        if choice.startswith(("a", "r")):
            _decide_org_change(company, proposal.id, choice.startswith("a"), by)
        _print()
    return 0


def cmd_org_decide(args: argparse.Namespace) -> int:
    from .api import Company

    if not _person_at_terminal():
        _print("Deciding changes to the company needs a person at a terminal. Nothing was changed.")
        return 3
    return _decide_org_change(Company(Path.cwd()), args.id, args.decision == "add", args.by or getpass.getuser())


def cmd_rules(args: argparse.Namespace) -> int:
    """Standing rules: what should always hold, for every role on every step. In Claude Code, just say it."""
    orchestrator = _orchestrator()
    try:
        company = orchestrator.company
        if args.rules_command == "add":
            if not _person_at_terminal():
                _print("Adding a rule needs a person at a terminal. In Claude Code, just say it in the chat.")
                return 3
            try:
                company.add_rule(args.text, by=args.by or getpass.getuser(), at=utc_now())
            except ValueError as error:
                _print(str(error))
                return 1
        elif args.rules_command == "remove" and company.remove_rule(args.number, at=utc_now()) is None:
            _print(f"There's no rule #{args.number}.")
            return 1
        rules = company.rules()
        if not rules:
            _print('No standing rules. Add one with: trypro rules add "<what should always hold>"')
        for number, text in rules:
            _print(f"#{number} {text}")
        return 0
    finally:
        orchestrator.close()


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
        _print(f"Then: trypro resume {result.run_id}")
    return {"done": 0, "blocked": 3}.get(result.status, 1)


def terminal_approver() -> Any:
    """Asks the person at this terminal before a role runs a command that changes something outside. None if no
    person is here, and then those commands don't run."""
    if not _person_at_terminal():
        return None
    import threading

    lock = threading.Lock()

    def approve(role: str, command: str) -> bool:
        with lock:  # Roles can work at the same time, and a person answers one question at a time.
            _print("")
            _print(f"{role} wants to run a command that changes something outside:")
            _print(f"  {command}")
            return input("Allow it? [y/N]: ").strip().lower().startswith("y")

    return approve


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
    computer = config.raw.get("computer_tools", True) is not False
    approve = terminal_approver()
    # With nobody at the terminal, a command that changes something outside waits for a person: trypro held.
    runner = ApiRunner(orchestrator, router=router, computer=computer, approve=approve, hold=approve is None)
    return runner, orchestrator


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
    # Designing a company needs what's in .env, and there's no trypro.yaml yet to load it.
    load_env_file(root)
    # One server per role, so they share one copy of the model. Roles stay apart in the gateway and each tool list.
    use_embedding_service(root / ".trypro")

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
        if args.coordinator:
            # After an upgrade, Claude Code's files catch up by themselves: nobody has to know to rewrite them.
            refresh_claude_code_files(config.root, org)
        return McpService(
            orchestrator,
            bound_role=args.role,
            coordinator=args.coordinator,
            computer=not args.sandboxed,
            root=config.root,
        )

    if args.role and args.coordinator:
        print("A server is bound to a role, or coordinates. It can't be both.", file=sys.stderr)
        return 2
    # A role can't design the company it works in.
    design = None if args.role else _Design(root)
    # stdout belongs to the protocol from here on, so nothing else may print to it.
    build_server(company, design, coordinator=args.coordinator).run("stdio")
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
        if call is not pull.proposal_status:
            # A tool call can't print while it waits, so a design once sat silent for half an hour. Say it instead.
            loaded = self._embedder is not None
            shortfall = memory_shortfall(GENERATION_AFTER_MODEL_LOADED_MB if loaded else GENERATION_PEAK_MB)
            if shortfall:
                raise ToolError(shortfall)
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
    written = write_claude_code_files(config.root, org, args.model, args.sandboxed)
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


def _print_efficiency(orchestrator: Orchestrator, run_id: str) -> None:
    from .core.efficiency import measure

    audit = orchestrator.audit_path(run_id)
    if audit.is_file():
        for line in measure(audit, run_id).lines():
            _print(line)
    proposed = orchestrator.company.org_proposals("pending", source_run=run_id)
    if proposed:
        _print("The CEO proposes changes to the company, from what this command showed:")
        for proposal in proposed:
            _print(f"  {proposal.line()}")
        _print("Only you decide them: trypro org review")


def _run_goal(orchestrator: Orchestrator, runner: ApiRunner, goal: str) -> int:
    wait_for_memory(COMMAND_PEAK_MB, say=_print)
    result = runner.run(goal)
    code = _print_result(result, orchestrator.deliverables_path(result.run_id))
    _print_efficiency(orchestrator, result.run_id)
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
            code = _print_result(runner.resume(args.run_id), orchestrator.deliverables_path(args.run_id))
            _print_efficiency(orchestrator, args.run_id)
            return code
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
            dropped = f", {item.source} for its team" if item.source.startswith(DROPPED_BY) else ""
            _print(f"#{item.id} company, from {item.proposed_by}{dropped} ({item.source_run}): {item.text}")
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
            dropped = f", {item.source} for its team" if item.source.startswith(DROPPED_BY) else ""
            _print(f"#{item.id} from {item.proposed_by}{dropped} ({item.source_run}):\n  {item.text}")
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
    # A real session with a shell once answered a budget question this way, for the person it was working for.
    if not _person_at_terminal():
        _print("Answering a run's question needs a person at a terminal. Nothing was recorded.")
        return 3
    orchestrator = _orchestrator()
    try:
        orchestrator.handle(
            args.run_id,
            HumanDecided(
                request_id=args.request_id,
                decision=args.decision,
                note=args.note or "",
                new_token_budget=args.token_budget,
                decided_by=args.by or "human",
                model=args.model,
            ),
        )
    except IllegalEvent as error:
        _print(str(error))
        return 1
    finally:
        orchestrator.close()
    if args.decision in ("abandon", "cancel"):
        _print("Recorded. That work is stopped, and its records stay.")
        return 0
    _print("Recorded. To continue, in Claude Code:")
    _print(f"  /company continue {args.run_id}")
    _print(f"With the API runner: trypro resume {args.run_id}")
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


def _limit(value: int | None) -> str:
    return "no limit" if value is None else f"{value:,}"


def _status_tokens(orchestrator: Orchestrator) -> None:
    budget = orchestrator.org.pipeline
    _print("Token limits, where a command stops and asks you (none unless org.yaml sets pipeline.budget):")
    small, large = _limit(budget.max_tokens_per_small_command), _limit(budget.max_tokens_per_run)
    _print(f"  API runner, real tokens:            {small} small, {large} large")
    small, large = _limit(budget.mcp_max_tokens_per_small_command), _limit(budget.mcp_max_tokens_per_run)
    _print(f"  Over MCP, the server's estimate x{MCP_ESTIMATE_FACTOR}: {small} small, {large} large")
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
        _print_efficiency(orchestrator, args.run_id)
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


def cmd_setup(args: argparse.Namespace) -> int:
    return setup_claude_code(Path(args.dir).resolve() if args.dir else Path.cwd(), _print)


def cmd_start(args: argparse.Namespace) -> int:
    """From an empty folder to a company at work: setup, a design, a person's approval, and the first job."""
    root = Path(args.dir).resolve() if args.dir else Path.cwd()
    request = " ".join(args.request).strip()
    if not request:
        _print('Say what you want made: trypro start "<what you want>"')
        return 2
    _setup.borrow_github_token(_print)
    code = setup_claude_code(root, _print, next_steps=False)
    if code:
        return code
    if (root / "org" / "org.yaml").exists():
        _print("This folder already has a company, so it gets your request.")
        write_claude_code_files(root, load_org(root / "org"))
    else:
        proposal = _setup.design_with_claude(root, request, _print)
        if proposal is None:
            return 1
        load_env_file(root)
        _write_project_files(root)
        config = load_config(root)
        embedder = FastEmbedEmbedder(config.memory.embedding_model)
        bundle = load_proposal(root, proposal)
        code = _review(bundle, root, embedder, by=getpass.getuser(), client=None, model="", discovery=True)
        if code or not (root / "org" / "org.yaml").exists():
            return code or 1
    if args.no_launch or not _person_at_terminal():
        _print("Ready. Start Claude Code here, and say what you want, in your own words:")
        _print(f"  claude    then: {request}")
        return 0
    _print("Opening Claude Code on your request. If it asks whether you trust this folder, say yes.")
    _print("Later, just start Claude Code here, and say what you want. The company remembers its earlier work.")
    return _setup.launch_claude(root, request)


def cmd_chat(args: argparse.Namespace) -> int:
    from .chat import terminal_chat

    root = Path(args.dir).resolve() if args.dir else Path.cwd()
    if not (root / "org" / "org.yaml").exists():
        _print('No company here yet. Make one with: trypro start "<what you want>"')
        return 2
    try:
        return terminal_chat(root, say=_print)
    except ClientError as error:
        _print(f"No model to talk to: {error}")
        _print("Set TRYPRO_LLM_BASE_URL, TRYPRO_LLM_API_KEY, and TRYPRO_LLM_MODEL in .env. Or open Claude Code in")
        _print("this folder, and just say what you want there.")
        return 2


def _here(args: argparse.Namespace) -> Path:
    root = Path(getattr(args, "dir", None) or ".").resolve()
    load_env_file(root)
    return root


def _outside_orchestrator(root: Path, *, with_memory: bool) -> Orchestrator:
    """Lessons from outcomes go to roles' memory, so settling one needs it. Listing doesn't."""
    config = load_config(root)
    org = load_org(config.org_dir)
    memory = _memory(config, org) if with_memory else None
    return Orchestrator(org, config.home, memory=memory)


def cmd_outcomes(args: argparse.Namespace) -> int:
    from .outcomes import check

    root = _here(args)
    action = args.outcomes_command or "list"
    orchestrator = _outside_orchestrator(root, with_memory=action in ("check", "set"))
    try:
        if action == "list":
            items = orchestrator.outside.outcomes(args.status)
            if not items:
                _print("Nothing tracked yet. Links an approved command prints are tracked, and roles can add theirs.")
                return 0
            for item in items:
                _print(f"#{item.id:<4} {item.status:<10} {item.kind:<12} {item.target or '-':<24} {item.ref}")
                if item.title or item.note:
                    _print(f"       {item.title}{'  - ' + item.note if item.note else ''}")
            return 0
        if action == "check":
            report = check(orchestrator)
            for settled in report.settled:
                _print(f"#{settled.id} {settled.status}: {settled.ref}. Every role that worked on it gets the lesson.")
            for url in report.follow_ups:
                _print(f"New replies on {url}: a follow-up is queued. `trypro schedule run` runs it.")
            for problem in report.problems:
                _print(f"Couldn't check: {problem}")
            _print(f"Checked {report.checked}.")
            return 1 if report.problems and not report.checked else 0
        if action == "add":
            tracked = orchestrator.track_outcome(
                args.run_id, args.role, args.ref, kind=args.kind or "", title=args.title or "", target=args.target or ""
            )
            if tracked is None:
                _print("Give a link, or a reference a person would know it by.")
                return 2
            _print(f"Tracking #{tracked.id}: {tracked.ref}" + (f", sent to {tracked.target}" if tracked.target else ""))
            return 0
        if orchestrator.outside.outcome(args.id) is None:
            _print(f"There's no outcome #{args.id}.")
            return 2
        changed = orchestrator.settle_outcome(args.id, args.status, args.note or "", by=args.by or getpass.getuser())
        _print(f"#{args.id} is {args.status}. The roles that worked on it get the lesson." if changed else "No change.")
        return 0
    finally:
        orchestrator.close()


def cmd_held(args: argparse.Namespace) -> int:
    from .adapters.api_runner.tools import execute

    root = _here(args)
    orchestrator = _outside_orchestrator(root, with_memory=False)
    try:
        action = args.held_command or "list"
        if action == "list":
            waiting = orchestrator.outside.held()
            if not waiting:
                _print("Nothing is waiting for your approval.")
            for item in waiting:
                _print(f"#{item.id}  {item.role}, run {item.run_id}:")
                _print(f"    {item.command}")
                for line in item.checks.splitlines():
                    _print(f"    | {line}")
            if waiting:
                _print(
                    "Approve one with `trypro held approve <number>`, or refuse it with `trypro held deny <number>`."
                )
            return 0
        held = orchestrator.outside.held_item(args.id)
        if held is None or held.status != "waiting":
            _print(f"#{args.id} isn't waiting for approval.")
            return 2
        if not _person_at_terminal():
            _print("Approving or refusing needs a person at a terminal, or in chat. Nothing was done.")
            return 2
        by = args.by or getpass.getuser()
        if action == "deny":
            orchestrator.deny_held(args.id, by=by)
            _print(f"Refused #{args.id}. It won't run.")
            return 0
        _print(f"Running #{args.id} for {held.role}: {held.command}")
        done = orchestrator.run_held(args.id, execute, by=by)
        _print(done.output if done else "It had already been decided.")
        return 0
    finally:
        orchestrator.close()


def cmd_schedule(args: argparse.Namespace) -> int:
    from .core.outside import describe_every, parse_every
    from .schedule import apply_entry, install_entry, notify, run_round, watch

    root = _here(args)
    action = args.schedule_command or "list"
    if action == "run":
        if args.watch:
            return watch(root, say=_print)
        outcome = run_round(root, say=_print, dry_run=args.dry_run)
        text = outcome.summary(load_org(load_config(root).org_dir).company_name)
        _print(text)
        if outcome.ran or outcome.settled or outcome.held:
            notify(text, _print)
        return 1 if outcome.busy else 0
    if action == "install":
        shown, _ = install_entry(root, args.every_minutes)
        if not args.apply:
            _print("This runs a round every few minutes, from your operating system's scheduler:")
            _print(f"  {shown}")
            _print("Add it yourself, or run this again with --apply.")
            return 0
        try:
            _print(apply_entry(root, args.every_minutes))
        except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
            _print(f"Couldn't add it: {error}")
            return 1
        return 0
    orchestrator = _outside_orchestrator(root, with_memory=False)
    try:
        if action == "add":
            try:
                every = parse_every(args.every)
                item = orchestrator.outside.add_schedule(args.name, args.goal, every, utc_now())
            except ValueError as error:
                _print(str(error))
                return 2
            _print(f"{item.name}: every {describe_every(item.every_seconds)}, first at the next round. {item.goal}")
            _print("Rounds run from your system's scheduler (trypro schedule install), or trypro schedule run --watch.")
            return 0
        if action == "remove":
            removed = orchestrator.outside.remove_schedule(args.name)
            _print(f"Removed {args.name}." if removed else f"There's no schedule called {args.name}.")
            return 0 if removed else 2
        schedules = orchestrator.outside.schedules()
        if not schedules:
            _print('No schedules. Add one: trypro schedule add <name> "<what to do>" --every 1d')
        for entry in schedules:
            last = f", last {entry.last_at[:16]} ({entry.last_run_id})" if entry.last_at else ""
            _print(f"{entry.name:<20} every {describe_every(entry.every_seconds):<4} next {entry.next_at[:16]}{last}")
            _print(f"    {entry.goal}")
        return 0
    finally:
        orchestrator.close()


def cmd_telegram(args: argparse.Namespace) -> int:
    from .telegram import TelegramError, serve_from_env

    root = Path(args.dir).resolve() if args.dir else Path.cwd()
    if not (root / "org" / "org.yaml").exists():
        _print('No company here yet. Make one with: trypro start "<what you want>"')
        return 2
    load_env_file(root)
    try:
        return serve_from_env(root, _print)
    except TelegramError as error:
        _print(str(error))
        return 1


def cmd_doctor(args: argparse.Namespace) -> int:
    checks = run_checks(Path.cwd())
    if args.report:
        _print(report(checks))
        return 0
    return 0 if print_checks(checks, _print) else 1


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
    proposals = org.add_parser("proposals", help="Roles and departments the CEO proposed, from what its runs showed")
    proposals.add_argument("--all", action="store_true", help="Also the ones already decided")
    proposals.set_defaults(func=cmd_org_proposals)
    review = org.add_parser("review", help="Decide the CEO's proposed roles and departments, at a terminal")
    review.add_argument("--by", help="Your name, for the record. Default: your username")
    review.set_defaults(func=cmd_org_review)
    decide = org.add_parser("decide", help="Add or reject one proposed role or department, at a terminal")
    decide.add_argument("id", type=int)
    decide.add_argument("decision", choices=("add", "reject"))
    decide.add_argument("--by", help="Your name, for the record. Default: your username")
    decide.set_defaults(func=cmd_org_decide)

    rules = sub.add_parser("rules", help="Standing rules every role follows on every step")
    rules.set_defaults(func=cmd_rules, rules_command=None)
    rules_sub = rules.add_subparsers(dest="rules_command")
    rules_add = rules_sub.add_parser("add", help="Add one, at a terminal")
    rules_add.add_argument("text")
    rules_add.add_argument("--by", help="Your name, for the record. Default: your username")
    rules_sub.add_parser("remove", help="Remove one by its number").add_argument("number", type=int)

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
    decide.add_argument("--by", help="Your name. A waiver needs it")
    decide.add_argument("--model", help="For a stuck role: another model to retry it with")
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
    mcp.add_argument(
        "--coordinator",
        action="store_true",
        help="Only start and follow work, and hand it to servers bound to each role. It can't do a role's work",
    )
    mcp.add_argument(
        "--sandboxed", action="store_true", help="The role has only the company's tools: no shell, files, or web"
    )
    mcp.set_defaults(func=cmd_mcp)

    outcomes = sub.add_parser("outcomes", help="What the company put out, and what came of it. Roles learn from it")
    outcomes.add_argument("--dir", help="The company's folder. Default: here")
    outcomes.add_argument("--status", choices=OUTCOME_STATUSES)
    outcomes.set_defaults(func=cmd_outcomes, outcomes_command=None)
    outcome_sub = outcomes.add_subparsers(dest="outcomes_command")
    outcome_sub.add_parser("check", help="Check GitHub issues and pull requests with gh, and queue follow-ups")
    outcome_add = outcome_sub.add_parser("add", help="Track something the company put out: a link, or any reference")
    outcome_add.add_argument("ref", help='A link, or a reference such as "quote 12, emailed"')
    outcome_add.add_argument(
        "--target", help="Who or what it went to: a client, a project, a site. Default: from the link"
    )
    outcome_add.add_argument("--run", dest="run_id", required=True, help="The run that made it")
    outcome_add.add_argument("--role", required=True, help="The role that put it out, such as outreach/poster")
    outcome_add.add_argument("--title")
    outcome_add.add_argument("--kind", help="Such as issue, post, or email. Default: from the link")
    outcome_set = outcome_sub.add_parser("set", help="Say what happened to something, when no check can")
    outcome_set.add_argument("id", type=int)
    outcome_set.add_argument("status", choices=OUTCOME_STATUSES)
    outcome_set.add_argument("--note", help="What was said, or why")
    outcome_set.add_argument("--by", help="Your name. Default: your username")

    held = sub.add_parser("held", help="Commands that change something outside, waiting for your approval")
    held.add_argument("--dir", help="The company's folder. Default: here")
    held.set_defaults(func=cmd_held, held_command=None)
    held_sub = held.add_subparsers(dest="held_command")
    for name, text in (("approve", "Run it now"), ("deny", "Refuse it")):
        decision = held_sub.add_parser(name, help=text)
        decision.add_argument("id", type=int)
        decision.add_argument("--by", help="Your name. Default: your username")

    schedule = sub.add_parser("schedule", help="Commands the company runs on a timer, unattended")
    schedule.add_argument("--dir", help="The company's folder. Default: here")
    schedule.set_defaults(func=cmd_schedule, schedule_command=None)
    schedule_sub = schedule.add_subparsers(dest="schedule_command")
    schedule_add = schedule_sub.add_parser("add", help="Add or replace a schedule")
    schedule_add.add_argument("name", help="One word, such as daily-hunt")
    schedule_add.add_argument("goal", help="What the company should do each time")
    schedule_add.add_argument("--every", required=True, help="Such as 6h, 1d, or 1w. At least 15m")
    schedule_remove = schedule_sub.add_parser("remove")
    schedule_remove.add_argument("name")
    schedule_sub.add_parser("list")
    schedule_run = schedule_sub.add_parser(
        "run", help="One round: check outcomes, run what's due and any follow-ups. Outside commands are held"
    )
    schedule_run.add_argument("--watch", action="store_true", help="Keep running a round every 5 minutes")
    schedule_run.add_argument("--dry-run", action="store_true", help="Use a scripted model: no key, no cost")
    schedule_install = schedule_sub.add_parser("install", help="Have your system's scheduler run a round regularly")
    schedule_install.add_argument("--apply", action="store_true", help="Add it now, instead of showing it")
    schedule_install.add_argument("--every-minutes", type=int, default=30)

    telegram = sub.add_parser("telegram", help="Talk to your company from Telegram. Needs a bot token in .env")
    telegram.add_argument("--dir", help="The company's folder. Default: here")
    telegram.set_defaults(func=cmd_telegram)

    chat = sub.add_parser("chat", help="Talk to your company: say what you want, and the CEO answers")
    chat.add_argument("--dir", help="The company's folder. Default: here")
    chat.set_defaults(func=cmd_chat)

    start = sub.add_parser(
        "start", help="Say what you want made: it designs a company, a person approves it, and it gets to work"
    )
    start.add_argument("request", nargs="+", help='What you want made, such as "a launch plan for my bakery"')
    start.add_argument("--dir", help="The company's folder. Default: here")
    start.add_argument("--no-launch", action="store_true", help="Stop once it's ready, without opening Claude Code")
    start.set_defaults(func=cmd_start)

    setup = sub.add_parser("setup", help="Get ready to design a company in a runtime: checks, then adds trypro to it")
    setup.add_argument("runtime", choices=["claude-code"])
    setup.add_argument("--dir", help="The company's folder. Default: here")
    setup.set_defaults(func=cmd_setup)

    doctor = sub.add_parser("doctor", help="Check this project's setup")
    doctor.add_argument(
        "--report", action="store_true", help="Print a diagnostic to paste into a bug report: no keys, paths, or names"
    )
    doctor.set_defaults(func=cmd_doctor)

    agents = sub.add_parser("agents", help="Set up an agent runtime to run this company")
    agents.add_argument("runtime", choices=["claude-code"])
    agents.add_argument(
        "--sandboxed",
        action="store_true",
        help="Roles get only the company's own tools, and no shell, files, or web. By default they're full agents",
    )
    agents.add_argument(
        "--model",
        choices=["sonnet", "opus", "haiku", "inherit"],
        help="One model for every role's subagent. By default, each role's tier chooses: haiku for fast, else sonnet",
    )
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
