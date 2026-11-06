"""The whole user journey on real businesses, as Phase 4c's exit test.

usage:
    python run_journey.py --dry-run <output_dir>
        Free: a scripted model and no GitHub. Tests the harness.
    TRYPRO_JOURNEY_APPROVED=yes python run_journey.py <output_dir> <max_tokens>
        PAID: stops spending at max_tokens.

For each business: generate and approve a company, run a large command, run a small one, approve what the
CEO proposed for company memory, then run a related command a simulated week later.

This script stands in for the person at the review screen, which a real user can't do from a script. It
measures flow and cost. Quality is judged by reading the deliverables it saves.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from try_professional.adapters.api_runner.clients import ChatRequest, ChatResponse, ClientError
from try_professional.adapters.api_runner.runner import ApiRunner, ModelRoutes
from try_professional.adapters.api_runner.scripted import ScriptedClient
from try_professional.cli import _memory, _seed_company_memory, _skills, _write_project_files
from try_professional.config import ModelConfig, build_client, load_config, load_env_file
from try_professional.core.orchestrator import Orchestrator
from try_professional.generator import activate, create_proposal, render
from try_professional.memory import FastEmbedEmbedder
from try_professional.org import load_org
from try_professional.skills import LockedSkills

BUSINESSES: dict[str, dict[str, Any]] = {
    "shorts": {
        "description": "a YouTube Shorts content company",
        "exclude": (),
        "large": "Make 5 Shorts about AI tools for this week.",
        "small": "Write one title for a Short about AI note-taking apps.",
        "later": "Make 3 more Shorts about AI tools, and build on what worked in last week's batch.",
    },
    "catering": {
        "description": "a catering business that delivers office lunches",
        "exclude": (),
        "large": "Write a weekly lunch menu and a price list for a new office client with 30 people.",
        "small": "Write a two-sentence reply to a customer asking whether we cover vegetarian meals.",
        "later": "Write next week's menu for the same office client, without repeating last week's dishes.",
    },
    "umkm-snacks": {
        "description": "UMKM Indonesia yang menjual keripik pisang secara online",
        "exclude": (),
        "large": "Buat rencana promosi Instagram selama dua minggu untuk keripik pisang kami.",
        "small": "Tulis satu balasan singkat untuk pelanggan yang bertanya ongkos kirim ke Surabaya.",
        "later": "Buat rencana promosi dua minggu berikutnya, dan lanjutkan dari rencana sebelumnya.",
    },
}


OUTAGE_WAIT_SECONDS = 180
OUTAGE_RETRIES = 20


class Stopped(ClientError):
    pass


class Capped:
    """Every model call in the journey goes through here, so its limit can't be passed by much."""

    def __init__(self, client: Any, limit: int) -> None:
        self._client = client
        self._limit = limit
        self.input = 0
        self.cached = 0
        self.output = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    @property
    def spent(self) -> int:
        return self.input + self.output

    def complete(self, request: ChatRequest) -> ChatResponse:
        if self.spent >= self._limit:
            raise Stopped(f"The journey's limit of {self._limit} tokens is spent.", retryable=False)
        for attempt in range(OUTAGE_RETRIES):
            try:
                response: ChatResponse = self._client.complete(request)
                break
            except ClientError as error:
                # An outage costs nothing, and shouldn't count against a company's work.
                outage = "HTTP 5" in str(error) or "request failed" in str(error)
                if not outage or attempt == OUTAGE_RETRIES - 1:
                    raise
                print(f"The provider is down, so waiting {OUTAGE_WAIT_SECONDS}s.", flush=True)
                time.sleep(OUTAGE_WAIT_SECONDS)
        self.input += response.input_tokens
        self.cached += response.cached_input_tokens
        self.output += response.output_tokens
        return response


def _usage(usage: Any) -> dict[str, int]:
    return {
        "input": usage.input_tokens,
        "cached_input": usage.cached_input_tokens,
        "output": usage.output_tokens,
        "calls": usage.calls,
    }


def _command(orchestrator: Orchestrator, runner: ApiRunner, goal: str, day: str, output: Path, label: str) -> dict:
    result = runner.run(goal)
    state = orchestrator.state(result.run_id)
    target = orchestrator.deliverables_path(result.run_id)
    saved = output / "deliverables" / label
    if target is not None and target.is_dir():
        shutil.copytree(target, saved, dirs_exist_ok=True)
    work = sum(u.total for p, u in state.usage_by_purpose.items() if p == "execute")
    review = sum(u.total for p, u in state.usage_by_purpose.items() if p == "review")
    tasks = [t for t in state.tasks.values() if t.work_package_id]
    return {
        "label": label,
        "day": day,
        "goal": goal,
        "run_id": result.run_id,
        "status": result.status,
        "reason": result.reason,
        "size": state.size,
        "tokens": _usage(state.usage),
        "by_purpose": {p: _usage(u) for p, u in state.usage_by_purpose.items()},
        "by_role": {r: _usage(u) for r, u in state.usage_by_role.items()},
        "coordination_tokens": state.usage.total - work - review,
        "work_tokens": work,
        "review_tokens": review,
        "departments": sorted(state.activated),
        "tasks": [
            {
                "id": t.task_id,
                "title": t.title,
                "owner": t.owner_role,
                "status": t.status.value,
                "rework": t.rework_count,
                "escalations": t.escalation_count,
                "memory_used": t.memory_context,
                "brief": state.artifacts[t.brief_artifact_id].payload if t.brief_artifact_id else None,
            }
            for t in tasks
        ],
        "counters": state.counters.model_dump(),
        "questions": [q.question for q in result.questions],
        "final": result.final,
    }


def run_business(business_id: str, output: Path, limit: int, *, dry_run: bool) -> None:
    business = BUSINESSES[business_id]
    root = output / "company"
    root.mkdir(parents=True, exist_ok=True)
    _write_project_files(root)
    embedder = FastEmbedEmbedder()
    if dry_run:
        client: Any = ScriptedClient()
        model = "scripted"
    else:
        load_env_file(Path.cwd())
        model = os.environ["TRYPRO_LLM_MODEL"]
        client = build_client(
            ModelConfig(
                provider="openai_compatible",
                tiers={},
                api_key_env="TRYPRO_LLM_API_KEY",
                base_url=os.environ["TRYPRO_LLM_BASE_URL"],
            )
        )
    capped = Capped(client, limit)
    bundle = create_proposal(
        root,
        embedder,
        description=str(business["description"]),
        company=None if not dry_run else business_id,
        client=None if dry_run else capped,
        model=model,
        discovery=not dry_run,
        provisioning=True,
        exclude_templates=tuple(business["exclude"]),
        say=print,
    )
    (output / "review-screen.txt").write_text(render(bundle), encoding="utf-8")
    design = dict(bundle.meta["tokens"])
    activate(bundle, root, embedder, by="the journey harness, standing in for a person")
    _seed_company_memory(bundle, root, "the journey harness")

    config = load_config(root)
    org = load_org(config.org_dir)
    today = {"value": "2026-11-09T09:00:00+00:00"}
    orchestrator = Orchestrator(
        org,
        config.home,
        memory=_memory(config, org) if not dry_run else None,
        skills=_skills(config, org),
        inputs_dir=root / "inputs",
        deliverables_dir=root / "deliverables",
        clock=lambda: today["value"],
    )
    if isinstance(orchestrator.skills, LockedSkills):
        orchestrator.feedback = orchestrator.skills
    routes = ModelRoutes({"standard": model})
    runner = ApiRunner(orchestrator, capped, routes)
    commands = []
    try:
        for label, day in (("large", "2026-11-09"), ("small", "2026-11-09"), ("later", "2026-11-16")):
            if capped.spent >= limit:
                break
            today["value"] = f"{day}T09:00:00+00:00"
            if label == "later":
                # The person approves what the CEO proposed, as a user would with `trypro memory review`.
                memory = orchestrator.memory
                for item in orchestrator.company.pending_memory("company"):
                    memory.promote(org.root_role, item.text, f"{item.source}, approved by the journey harness")
                    orchestrator.company.decide_memory(item.id, True, by="the journey harness", at=today["value"])
            print(f"[{business_id}] {label}: {business[label]}", flush=True)
            commands.append(_command(orchestrator, runner, str(business[label]), day, output, label))
        result = {
            "business": business_id,
            "description": business["description"],
            "dry_run": dry_run,
            "model": model,
            "template": bundle.meta["template"],
            "template_match": bundle.meta["template_match"],
            "roles": {
                ref: {"title": org.role(ref).display_name, "head": org.is_head(ref)} for ref in sorted(org.roles)
            },
            "design_tokens": design,
            "journey_tokens": {"input": capped.input, "cached_input": capped.cached, "output": capped.output},
            "commands": commands,
            "pending_team_lessons": [p.text for p in orchestrator.company.pending_memory("department")],
            "memory_sizes": orchestrator.memory.sizes(),
            "estimate": bundle.estimate.to_dict(),
        }
    finally:
        orchestrator.close()
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    if len(args) >= 3 and args[0] == "--business":
        run_business(args[1], Path(args[2]), int(args[3]), dry_run=dry_run)
        return
    if not dry_run and os.environ.get("TRYPRO_JOURNEY_APPROVED") != "yes":
        raise SystemExit("This calls the paid API. Set TRYPRO_JOURNEY_APPROVED=yes once the owner has approved it.")
    output = Path(args[0])
    limit = int(args[1]) if len(args) > 1 else 10**9
    spent = 0
    summary = []
    for business_id in BUSINESSES:
        if spent >= limit:
            summary.append({"business": business_id, "skipped": "The limit was spent."})
            continue
        folder = output / business_id
        folder.mkdir(parents=True, exist_ok=True)
        # Each company gets its own process, because the memory library's settings are per process.
        command = [sys.executable, __file__, "--business", business_id, str(folder), str(limit - spent)]
        code = subprocess.run([*command, *(["--dry-run"] if dry_run else [])], check=False).returncode
        result_path = folder / "result.json"
        if code != 0 or not result_path.is_file():
            summary.append({"business": business_id, "failed": f"exit code {code}"})
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        tokens = result["journey_tokens"]
        spent += tokens["input"] + tokens["output"]
        summary.append({"business": business_id, "tokens": tokens})
    (output / "summary.json").write_text(
        json.dumps({"spent": spent, "businesses": summary}, indent=1), encoding="utf-8"
    )
    print(json.dumps({"spent": spent}))


if __name__ == "__main__":
    main()
