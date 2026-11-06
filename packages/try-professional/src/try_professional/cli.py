"""The trypro command."""

from __future__ import annotations

import argparse
import json
import os
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
from .adapters.mcp import McpService
from .config import CONFIG_NAME, CONFIG_TEMPLATE, ClientRouter, ProjectConfig, load_config
from .core.audit import read_records
from .core.orchestrator import Orchestrator
from .core.pipeline import HumanDecided, IllegalEvent
from .memory import (
    FastEmbedEmbedder,
    MemoryConfigError,
    MemoryGateway,
    MemorySettings,
    MnemosyneBackend,
    check_local_only,
    model_cache_dir,
    score_floor,
)
from .org import Org, OrgValidationError, load_org, validate_bundle
from .org.loader import read_documents
from .packs import add_pack, available_packs, install_org, remove_pack
from .skills import (
    DEFAULT_UNUSED_RUNS,
    UNCATEGORIZED,
    Decisions,
    LoadoutStore,
    LockedSkills,
    ProposalWriter,
    SkillCatalog,
    match_all,
    sync_catalog,
)


def _print(text: str = "") -> None:
    """Print any language, even on a console whose encoding can't show it."""
    stream = sys.stdout
    encoding = stream.encoding or "utf-8"
    stream.write(text.encode(encoding, errors="backslashreplace").decode(encoding) + "\n")


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
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


def _print_result(result: RunResult) -> int:
    _print(f"Run {result.run_id}: {result.status}")
    total = result.input_tokens + result.output_tokens
    cached = f", {result.cached_input_tokens} of the input cached" if result.cached_input_tokens else ""
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
    if args.dry_run:
        orchestrator = Orchestrator(org, config.home)
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


def _sync_and_match(config: ProjectConfig, org: Org) -> tuple[dict[str, Any], LoadoutStore] | None:
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
    loadouts = match_all(org, catalog, store, decisions)
    catalog.close()
    return loadouts, store


def cmd_skills_match(args: argparse.Namespace) -> int:
    config = load_config(Path.cwd())
    org = load_org(config.org_dir)
    result = _sync_and_match(config, org)
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
    config = load_config(Path.cwd())
    org = load_org(config.org_dir)
    orchestrator = Orchestrator(
        org,
        config.home,
        memory=_memory(config, org),
        skills=_skills(config, org),
        proposals=ProposalWriter(config.root / "skills" / "proposed"),
    )
    if isinstance(orchestrator.skills, LockedSkills):
        orchestrator.feedback = orchestrator.skills
    # stdout belongs to the protocol from here on, so nothing else may print to it.
    build_server(McpService(orchestrator, bound_role=args.role)).run("stdio")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    runner, orchestrator = _runner(args)
    try:
        return _print_result(runner.run(args.goal))
    finally:
        orchestrator.close()


def cmd_resume(args: argparse.Namespace) -> int:
    runner, orchestrator = _runner(args)
    try:
        return _print_result(runner.resume(args.run_id))
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


def cmd_status(args: argparse.Namespace) -> int:
    orchestrator = _orchestrator()
    try:
        if not args.run_id:
            runs = orchestrator.ledger.list_runs()
            if not runs:
                _print("No runs yet.")
            for run_id, updated in runs:
                state = orchestrator.state(run_id)
                _print(f"{run_id}  {state.phase.value:<16} {updated}  {state.goal[:60]}")
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

    init = sub.add_parser("init", help="Create a company with the default org")
    init.add_argument("--dir", default=".")
    init.add_argument("--company")
    init.add_argument("--packs", help="Comma-separated packs. Default: all")
    init.add_argument("--force", action="store_true")
    init.add_argument("--skip-model", action="store_true", help="Don't download the embedding model now")
    init.set_defaults(func=cmd_init)

    org = sub.add_parser("org", help="Inspect the organization").add_subparsers(dest="org_command", required=True)
    validate = org.add_parser("validate", help="Validate against spec org/v1")
    validate.add_argument("path", nargs="?")
    validate.set_defaults(func=cmd_org_validate)
    org.add_parser("show", help="Print the org chart").set_defaults(func=cmd_org_show)

    pack = sub.add_parser("pack", help="Manage department packs").add_subparsers(dest="pack_command", required=True)
    pack.add_parser("list").set_defaults(func=cmd_pack)
    for name in ("add", "remove"):
        item = pack.add_parser(name)
        item.add_argument("name")
        item.set_defaults(func=cmd_pack)

    run = sub.add_parser("run", help="Run a goal")
    run.add_argument("goal")
    run.add_argument("--dry-run", action="store_true", help="Use a scripted model: no key, no cost")
    run.set_defaults(func=cmd_run)

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

    status = sub.add_parser("status", help="Show runs, or one run")
    status.add_argument("run_id", nargs="?")
    status.set_defaults(func=cmd_status)

    audit = sub.add_parser("audit", help="Verify a run's audit log")
    audit.add_argument("run_id")
    audit.add_argument("--show", action="store_true")
    audit.set_defaults(func=cmd_audit)

    skills = sub.add_parser("skills", help="Agent skills").add_subparsers(dest="skills_command", required=True)
    match = skills.add_parser("match", help="Rebuild the catalog from skills.lock and recompute every loadout")
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
    mcp.set_defaults(func=cmd_mcp)

    sub.add_parser("doctor", help="Check this project's setup").set_defaults(func=cmd_doctor)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result: int = args.func(args)
        return result
    except (
        FileNotFoundError,
        KeyError,
        OrgValidationError,
        ClientError,
        MemoryConfigError,
        SkillCustomsError,
    ) as error:
        _print(f"Error: {error.args[0] if error.args else error}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
