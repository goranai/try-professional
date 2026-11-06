"""Estimate the paid tests for Phase 4b from real prompt sizes. Free: no model is called.

usage: python estimate_paid_tests.py <proposal_dir_with_a_catalog>

Prompts are counted as characters / 3.5, a little pessimistic for JSON-heavy English. Output sizes
are assumptions, and each one is printed so it can be challenged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from try_professional.generator import compile_org, estimate_run, load_template, template_ids
from try_professional.generator.architect import submit_spec, system_prompt, user_prompt
from try_professional.memory import FastEmbedEmbedder
from try_professional.org import load_bundle
from try_professional.skills import Decisions, SkillCatalog, shortlist
from try_professional.skills.rerank import _SUBMIT, _SYSTEM, SHORTLIST_SIZE, judge_prompt

CHARS_PER_TOKEN = 3.5
ARCHITECT_RETRIES_WORST = 3
# The judge lists only the skills that fit, at about 40 tokens each. A role's cap is 8.
FIT_OUTPUT_TOKENS = 450
GENERATIONS = 24
SPLIT_ROLES = 22


def tokens(chars: int) -> int:
    return int(chars / CHARS_PER_TOKEN)


def main(proposal: Path) -> None:
    catalog = SkillCatalog(proposal / "catalog.sqlite", FastEmbedEmbedder())
    decisions = Decisions(proposal / "none.yaml")

    print("Architect, by starting template:")
    tool = len(json.dumps(submit_spec().input_schema))
    architect = []
    for template_id in template_ids():
        template = load_template(template_id)
        prompt = len(system_prompt()) + len(user_prompt("x" * 90, template, None, "")) + tool
        compiled = compile_org(template, template.keep_all("X"))
        full_output = sum(len(yaml.safe_dump(d)) for d in compiled.roles.values())
        architect.append(tokens(prompt) + tokens(full_output))
        print(f"  {template_id}: {tokens(prompt)} in, at most about {tokens(full_output)} out if it rewrote every role")
    typical = max(architect)
    print(f"  Per generation, one try, worst template: {typical}. With 3 tries: {typical * ARCHITECT_RETRIES_WORST}.")

    print("Re-rank, per role, from the real catalog:")
    shorts = load_template("youtube-shorts-studio")
    compiled = compile_org(shorts, shorts.keep_all("X"))
    org = load_bundle(compiled.org, compiled.roles)
    lists = shortlist(org, catalog.eligible(), catalog.embedder, decisions)
    judge_tool = len(json.dumps(_SUBMIT.input_schema))
    per_role = []
    for ref, candidates in lists.items():
        if candidates:
            prompt = len(_SYSTEM) + judge_tool + len(judge_prompt(org.role(ref), candidates))
            per_role.append(tokens(prompt) + FIT_OUTPUT_TOKENS)
    worst_role = tokens(len(_SYSTEM) + judge_tool + 700 + SHORTLIST_SIZE * 330) + FIT_OUTPUT_TOKENS
    catalog.close()
    print(f"  This catalog ({len(per_role)} roles with candidates): average {sum(per_role) // len(per_role)} per role.")
    print(f"  A full shortlist of {SHORTLIST_SIZE} with long descriptions: {worst_role} per role.")

    roles_typical = 17
    roles_worst = 25
    rerank_typical = worst_role * roles_typical
    rerank_worst = worst_role * roles_worst
    print(f"  Per company: {rerank_typical} at {roles_typical} roles, {rerank_worst} at the {roles_worst}-role limit.")

    run = estimate_run(org)
    print(f"Shorts run: estimate {run.low}-{run.high}, ceiling {run.ceiling}.")

    real_skills = worst_role * SPLIT_ROLES
    print("Plan, in tokens:")
    print(f"  A. Re-rank on real skills, tune, held-out, and combined: about {real_skills * 3}")
    print(f"  B. {GENERATIONS} generations, design only: {typical * GENERATIONS}-{typical * 3 * GENERATIONS}")
    print(f"  C. Re-rank for {GENERATIONS} generations: about {rerank_typical * GENERATIONS}, up to {rerank_worst * GENERATIONS}")
    print(f"  C2. Re-rank for only the first generation of each of 8 businesses: about {rerank_typical * 8}")
    print(f"  D. Shorts: one design and re-rank ({typical + rerank_typical}), and a run up to its ceiling ({run.ceiling})")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
