"""Measure skill matching on the labelled sets: the shortlist, then the judge, as a Claude Code session would judge.

usage:
    python run_quality.py shortlist <split>              Free: how many labelled matches each shortlist keeps.
    python run_quality.py judge <split> <label> <out>    On a Claude plan: `claude -p` judges every role once.

The judge is Claude Code itself, through `claude -p` with no tools, given the same instructions and assignment the
product hands a Claude Code session to judge (`judge_system` and `judge_prompt`). It answers with the same decisions
the `submit_fit` tool takes. Categories are hidden, and the company has no business description, as in earlier
measurements, so the numbers compare.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from skill_customs import LicenseRejected, Lockfile, pin
from try_professional.adapters.api_runner.clients import ChatRequest, ChatResponse, ClientError, ToolCall
from try_professional.generator.provision import _SharedTarballs
from try_professional.memory import FastEmbedEmbedder
from try_professional.org import load_org
from try_professional.packs import available_packs, install_org
from try_professional.skills import UNCATEGORIZED, Decisions, RerankCache, SkillCatalog, records_from_lock, rerank
from try_professional.skills import shortlist as make_shortlist
from try_professional.skills.rerank import SUBMIT_FIT

HERE = Path(__file__).parent
MODEL = "claude-sonnet-5-5"
SIZES = (5, 10, 15, 20, 30)
LIMIT_WAIT_SECONDS = 600


def _claude() -> str:
    return shutil.which("claude") or shutil.which("claude.cmd") or "claude"


class ClaudeCodeJudge:
    """A judge client that hands each role's assignment to `claude -p`, and reads its decisions back."""

    def __init__(self) -> None:
        self.folder = tempfile.mkdtemp(prefix="trypro-judge-")

    def user_message(self, text: str) -> Any:
        return {"role": "user", "content": text}

    def tool_results_messages(self, results: list[Any]) -> list[Any]:
        return []

    def complete(self, request: ChatRequest) -> ChatResponse:
        tool = request.tools[0]
        assignment = "\n\n".join(str(m["content"]) for m in request.messages)
        prompt = (
            f"{request.system}\n\n{assignment}\n\nYou have no tools here. Instead of calling {tool.name}, reply with "
            f"only the JSON object it takes, and nothing else. Its schema:\n{json.dumps(tool.input_schema)}"
        )
        command = [
            *[_claude(), "-p", "--model", MODEL, "--output-format", "json", "--tools", ""],
            *["--strict-mcp-config", "--no-session-persistence"],
        ]
        for _ in range(12):
            # In an empty folder, so no project instructions or files reach the judge.
            done = subprocess.run(
                command, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=900, cwd=self.folder
            )
            try:
                out = json.loads(done.stdout)
            except json.JSONDecodeError:
                out = {}
            text = str(out.get("result", ""))
            if "limit" in text.lower() and out.get("is_error"):
                print(f"Claude usage limit, waiting {LIMIT_WAIT_SECONDS}s", flush=True)
                time.sleep(LIMIT_WAIT_SECONDS)
                continue
            if done.returncode != 0 and not text:
                raise ClientError(f"claude -p failed: {done.stderr.strip()[:300]}")
            break
        usage = out.get("usage", {})
        match = re.search(r"\{.*\}", text, re.DOTALL)
        calls = []
        if match:
            try:
                calls = [ToolCall(id="judge", name=SUBMIT_FIT, arguments=json.loads(match.group(0)))]
            except json.JSONDecodeError:
                calls = []
        return ChatResponse(
            text=text,
            tool_calls=calls,
            stop_reason="tool_use" if calls else "end_turn",
            assistant_message={"role": "assistant", "content": text},
            input_tokens=int(usage.get("input_tokens", 0)) + int(usage.get("cache_creation_input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            cached_input_tokens=int(usage.get("cache_read_input_tokens", 0)),
            model=MODEL,
        )


def prepare(split: str) -> tuple[Path, Any, SkillCatalog, dict[str, list[str]]]:
    sampled = json.loads((HERE / "sampled.json").read_text(encoding="utf-8"))
    labels_by_name = yaml.safe_load((HERE / "labels.yaml").read_text(encoding="utf-8"))
    chosen = [*sampled["tune"], *sampled["heldout"]] if split == "all" else sampled[split]
    names = {**labels_by_name["tune"], **labels_by_name["heldout"]}
    work = Path(tempfile.mkdtemp(prefix="trypro-quality-"))
    install_org(work / "org", "Quality", available_packs())
    org = load_org(work / "org")
    lock = Lockfile(work / "skills.lock")
    client = _SharedTarballs(work)
    labels: dict[str, list[str]] = {}
    for item in chosen:
        try:
            result = pin(item["repo"], item["commit"], path=item["path"], dest=work / "skills", client=client)
        except LicenseRejected:
            print(f"license refused: {item['path']}")
            continue
        lock.add(result.entry)
        labels[result.entry.skill_id] = names[Path(item["path"]).name]
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
        lists = make_shortlist(org, catalog.eligible(), catalog.embedder, Decisions(work / "n.yaml"), size=size)
        found = {(role, c.record.skill_id) for role, items in lists.items() for c in items}
        pairs = sum(len(items) for items in lists.values())
        print(f"size {size:>2}: {len(found & wanted)}/{len(wanted)} labelled matches survive, {pairs} pairs to judge")
    catalog.close()


def measure_judge(split: str, label: str, out: Path) -> None:
    work, org, catalog, labels = prepare(split)
    wanted = wanted_pairs(labels)
    lists = make_shortlist(org, catalog.eligible(), catalog.embedder, Decisions(work / "n.yaml"))
    shortlisted = {(role, c.record.skill_id) for role, items in lists.items() for c in items}
    cache = RerankCache(work / "verdicts.sqlite")
    started = time.time()
    verdicts, report = rerank(org, lists, ClaudeCodeJudge(), MODEL, cache)
    got = set()
    for role in org.roles:
        fits = [c for c in lists[role] if (v := verdicts.get((role, c.record.skill_id))) and v.fit]
        got |= {(role, c.record.skill_id) for c in fits[: org.role(role).max_skills]}
    correct = len(got & wanted)
    result = {
        "split": split,
        "model": MODEL,
        "skills": len(labels),
        "labelled_matches": len(wanted),
        "suggested": len(got),
        "correct": correct,
        "precision": round(correct / len(got), 3) if got else None,
        "recall": round(correct / len(wanted), 3) if wanted else None,
        "kept_by_shortlist": len(shortlisted & wanted),
        "wrong": sorted(f"{r} <- {s}" for r, s in got - wanted),
        "missed": sorted(
            f"{r} <- {s}" + ("" if (r, s) in shortlisted else " (not shortlisted)") for r, s in wanted - got
        ),
        "calls": report.calls,
        "input_tokens": report.input_tokens,
        "cached_input_tokens": report.cached_input_tokens,
        "output_tokens": report.output_tokens,
        "failed_roles": report.failed_roles,
        "chose_none": report.chose_none,
        "seconds": round(time.time() - started),
    }
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    data[label] = result
    out.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    shutil.copyfile(cache.raw_log, out.with_name(f"{out.stem}_{label}.raw.jsonl"))
    print(json.dumps({k: v for k, v in result.items() if k not in ("wrong", "missed")}))
    catalog.close()


if __name__ == "__main__":
    if sys.argv[1] == "shortlist":
        measure_shortlist(sys.argv[2])
    elif sys.argv[1] == "judge":
        measure_judge(sys.argv[2], sys.argv[3], Path(sys.argv[4]))
    else:
        raise SystemExit(__doc__)
