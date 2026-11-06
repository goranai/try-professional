"""The trypro command."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .adapters.api_runner.clients import ClientError
from .adapters.api_runner.runner import ApiRunner, ModelRoutes, RunResult
from .adapters.api_runner.scripted import ScriptedClient
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
    orchestrator = Orchestrator(org, config.home, memory=_memory(config, org))
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

    sub.add_parser("doctor", help="Check this project's setup").set_defaults(func=cmd_doctor)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result: int = args.func(args)
        return result
    except (FileNotFoundError, KeyError, OrgValidationError, ClientError, MemoryConfigError) as error:
        _print(f"Error: {error.args[0] if error.args else error}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
