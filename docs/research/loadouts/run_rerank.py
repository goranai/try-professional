"""Measure the shortlist and the model's re-rank on real public skills, at pinned commits.

usage:
    python run_rerank.py shortlist <split>             Free: how many labelled matches survive each shortlist size.
                                                       Don't use this on an unscored set: it reveals part of its score.
    python run_rerank.py judge <split> <output.json>   PAID: calls the model. Needs TRYPRO_RERANK_APPROVED=yes.
    python run_rerank.py role <split> <role> <output.jsonl>
                                                       PAID: judges one role, and saves its raw answer.
    python run_rerank.py size <split>                  Free: estimated tokens for a judge run. Reveals no match.

TRYPRO_RERANK_CEILING stops a paid run once it has spent that many tokens. Raw answers are saved beside the output.

Categories are hidden here on purpose, because a fresh install won't have a person's categories.
Tools are set aside, as in run_real.py, so this measures matching alone.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml
from skill_customs import LicenseRejected, Lockfile, pin

from try_professional.adapters.api_runner.clients import ChatRequest, ChatResponse, ClientError
from try_professional.config import ModelConfig, build_client, load_env_file
from try_professional.generator.provision import _SharedTarballs
from try_professional.memory import FastEmbedEmbedder
from try_professional.org import load_org
from try_professional.packs import available_packs, install_org
from try_professional.skills import (
    UNCATEGORIZED,
    Decisions,
    RerankCache,
    SkillCatalog,
    records_from_lock,
    rerank,
    shortlist,
)

from try_professional.skills.rerank import judge_prompt

HERE = Path(__file__).parent
SIZES = (5, 8, 10, 12, 15, 20)
CHARS_PER_TOKEN = 3.5
SYSTEM_AND_TOOL_TOKENS = 330
OUTPUT_TOKENS_PER_ROLE = 135


class Ceiling:
    """Refuses further calls once a run has spent its tokens, so no estimate can overrun."""

    def __init__(self, client: Any, limit: int) -> None:
        self._client = client
        self._limit = limit
        self.spent = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def complete(self, request: ChatRequest) -> ChatResponse:
        if self.spent >= self._limit:
            raise ClientError(f"token ceiling of {self._limit} reached")
        response: ChatResponse = self._client.complete(request)
        self.spent += response.input_tokens + response.output_tokens
        return response


def prepare(split: str) -> tuple:
    sets = yaml.safe_load((HERE / "real_skills.yaml").read_text(encoding="utf-8"))
    # "all" is the harder test: a larger catalog, so a role's shortlist has to choose.
    labelled = [*sets["tune"], *sets["heldout"]] if split == "all" else sets[split]
    work = Path(tempfile.mkdtemp(prefix="trypro-rerank-"))
    install_org(work / "org", "Real", available_packs())
    org = load_org(work / "org")
    lock = Lockfile(work / "skills.lock")
    client = _SharedTarballs(work)
    labels: dict[str, list[str]] = {}
    for item in labelled:
        try:
            result = pin(item["repo"], item["commit"], path=item["path"], dest=work / "skills", client=client)
        except LicenseRejected:
            continue
        lock.add(result.entry)
        labels[result.entry.skill_id] = item["roles"]
    records = [
        replace(r, trust="indexed", category=UNCATEGORIZED, tools=()) for r in records_from_lock(lock, work / "skills")
    ]
    catalog = SkillCatalog(work / "c.sqlite", FastEmbedEmbedder())
    catalog.rebuild(records)
    return work, org, catalog, labels


def wanted_pairs(labels: dict[str, list[str]]) -> set[tuple[str, str]]:
    return {(role, skill) for skill, roles in labels.items() for role in roles}


def measure_shortlist(split: str) -> None:
    work, org, catalog, labels = prepare(split)
    wanted = wanted_pairs(labels)
    for size in SIZES:
        lists = shortlist(org, catalog.eligible(), catalog.embedder, Decisions(work / "n.yaml"), size=size)
        found = {(role, c.record.skill_id) for role, items in lists.items() for c in items}
        pairs = sum(len(items) for items in lists.values())
        print(f"size {size:>2}: {len(found & wanted)}/{len(wanted)} labelled matches survive, {pairs} pairs to judge")
    catalog.close()


def paid_client() -> tuple[Any, str]:
    if os.environ.get("TRYPRO_RERANK_APPROVED") != "yes":
        raise SystemExit("This calls the paid API. Set TRYPRO_RERANK_APPROVED=yes once the owner has approved it.")
    load_env_file(Path.cwd())
    client = build_client(
        ModelConfig(
            provider="openai_compatible",
            tiers={},
            api_key_env="TRYPRO_LLM_API_KEY",
            base_url=os.environ["TRYPRO_LLM_BASE_URL"],
        )
    )
    if limit := os.environ.get("TRYPRO_RERANK_CEILING"):
        client = Ceiling(client, int(limit))
    return client, os.environ["TRYPRO_LLM_MODEL"]


def estimate_size(split: str) -> None:
    work, org, catalog, _ = prepare(split)
    lists = shortlist(org, catalog.eligible(), catalog.embedder, Decisions(work / "n.yaml"))
    roles = [ref for ref, items in lists.items() if items]
    prompt = sum(len(judge_prompt(org.role(ref), lists[ref])) for ref in roles) / CHARS_PER_TOKEN
    total = prompt + len(roles) * (SYSTEM_AND_TOOL_TOKENS + OUTPUT_TOKENS_PER_ROLE)
    print(f"{len(roles)} roles to judge, about {round(total)} tokens")
    catalog.close()


def measure_one_role(split: str, ref: str, output: Path) -> None:
    client, model = paid_client()
    work, org, catalog, _ = prepare(split)
    lists = shortlist(org, catalog.eligible(), catalog.embedder, Decisions(work / "n.yaml"))
    cache = RerankCache(work / "verdicts.sqlite")
    _, report = rerank(org, {ref: lists[ref]}, client, model, cache)
    shutil.copyfile(cache.raw_log, output)
    summary = {"role": ref, "tokens": report.total_tokens, "calls": report.calls}
    print(json.dumps({**summary, "failed": report.failed_roles, "chose_none": report.chose_none}))
    catalog.close()


def measure_judge(split: str, output: Path) -> None:
    client, model = paid_client()
    work, org, catalog, labels = prepare(split)
    wanted = wanted_pairs(labels)
    lists = shortlist(org, catalog.eligible(), catalog.embedder, Decisions(work / "n.yaml"))
    cache = RerankCache(work / "verdicts.sqlite")
    verdicts, report = rerank(org, lists, client, model, cache)
    shutil.copyfile(cache.raw_log, output.with_suffix(".raw.jsonl"))
    # A role's cap applies, as it does for a real loadout.
    got = set()
    for role in org.roles:
        fits = [c for c in lists[role] if (v := verdicts.get((role, c.record.skill_id))) and v.fit]
        got |= {(role, c.record.skill_id) for c in fits[: org.role(role).max_skills]}
    correct = len(got & wanted)
    precision = correct / len(got) if got else 0.0
    recall = correct / len(wanted) if wanted else 0.0
    result = {
        "split": split,
        "model": model,
        "skills": len(labels),
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "correct": correct,
        "wrong": len(got - wanted),
        "missed": len(wanted - got),
        "tokens": report.total_tokens,
        "input_tokens": report.input_tokens,
        "output_tokens": report.output_tokens,
        "cached_input_tokens": report.cached_input_tokens,
        "calls": report.calls,
        "failed_roles": report.failed_roles,
        "chose_none": report.chose_none,
        "rows": [
            {
                "role": role,
                "skill": skill.rsplit("/", 1)[-1],
                "fit": verdict.fit,
                "labelled": (role, skill) in wanted,
                "reason": verdict.reason,
            }
            for (role, skill), verdict in sorted(verdicts.items())
            if verdict.fit or (role, skill) in wanted
        ],
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}))
    catalog.close()


if __name__ == "__main__":
    if sys.argv[1] == "shortlist":
        measure_shortlist(sys.argv[2])
    elif sys.argv[1] == "size":
        estimate_size(sys.argv[2])
    elif sys.argv[1] == "role":
        measure_one_role(sys.argv[2], sys.argv[3], Path(sys.argv[4]))
    else:
        measure_judge(sys.argv[2], Path(sys.argv[3]))
