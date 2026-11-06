"""PAID: generate each business's company three times, and record everything for a person to judge.

usage: TRYPRO_EVAL_APPROVED=yes python run_eval.py <output_dir> <max_tokens>

It stops before starting a generation once it has spent max_tokens, and marks the results as stopped.
Only the first generation of each business finds skills and pays for the re-rank, as the owner chose,
and the other two show whether the design is consistent. Close templates are held out for the businesses
meant to have none, so those really do start from further away.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from try_professional.config import ModelConfig, build_client, load_env_file
from try_professional.generator import ArchitectFailed, create_proposal, render
from try_professional.memory import FastEmbedEmbedder

GENERATIONS = 3
BUSINESSES = (
    # The owner's three, with the closest template held out.
    {"id": "fashion-store", "description": "An online fashion store", "exclude": ("ecommerce-store",)},
    {"id": "digital-agency", "description": "A digital marketing agency", "exclude": ("marketing-agency",)},
    {
        "id": "indonesian-education",
        "description": "An Indonesian-language education channel on YouTube for high school students",
        "exclude": ("youtube-shorts-studio",),
    },
    # No template comes close to this one.
    {"id": "physio-clinic", "description": "A neighbourhood physiotherapy clinic", "exclude": ()},
    # Each of these has a close template, to see whether the Architect adapts it sensibly.
    {"id": "shorts", "description": "A YouTube Shorts content company", "exclude": ()},
    {"id": "saas-invoicing", "description": "A SaaS startup selling invoicing software to freelancers", "exclude": ()},
    {"id": "candle-shop", "description": "An online store selling handmade candles", "exclude": ()},
    {"id": "pr-agency", "description": "A small public relations agency for technology companies", "exclude": ()},
)


def main(output: Path, max_tokens: int) -> None:
    if os.environ.get("TRYPRO_EVAL_APPROVED") != "yes":
        raise SystemExit("This calls the paid API. Set TRYPRO_EVAL_APPROVED=yes once the owner has approved it.")
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
    embedder = FastEmbedEmbedder()
    output.mkdir(parents=True, exist_ok=True)
    spent = 0
    stopped = False
    results = []
    for business in BUSINESSES:
        root = output / "work" / str(business["id"])
        root.mkdir(parents=True, exist_ok=True)
        for generation in range(GENERATIONS):
            if spent >= max_tokens:
                stopped = True
                break
            row: dict = {"business": business["id"], "description": business["description"], "generation": generation}
            try:
                bundle = create_proposal(
                    root,
                    embedder,
                    description=str(business["description"]),
                    company=None,
                    client=client,
                    model=model,
                    discovery=generation == 0,
                    provisioning=generation == 0,
                    exclude_templates=tuple(business["exclude"]),
                    say=print,
                )
            except ArchitectFailed as error:
                row["failed"] = str(error)
                results.append(row)
                continue
            tokens = bundle.meta["tokens"]
            spent += tokens["architect"]["total"] + tokens["rerank"]["total"]
            row.update(
                {
                    "template": bundle.meta["template"],
                    "template_match": bundle.meta["template_match"],
                    "tokens": tokens,
                    "first_try_valid": tokens["architect"]["calls"] == 1,
                    "departments": [
                        {
                            "id": d.id,
                            "name": d.name,
                            "head": d.head,
                            "roles": [
                                {
                                    "id": r,
                                    "mission": bundle.org.role(f"{d.id}/{r}").mission,
                                    "skills": [
                                        {"skill": m.skill_id, "reason": m.reason, "category": m.category}
                                        for m in bundle.provisioned.matches.get(f"{d.id}/{r}", [])
                                    ],
                                    "gaps": bundle.provisioned.gaps.get(f"{d.id}/{r}", []),
                                }
                                for r in d.roles
                            ],
                        }
                        for d in bundle.org.departments.values()
                    ],
                    "quarantined": bundle.provisioned.quarantined,
                    "cannot_run": bundle.provisioned.cannot_run,
                    "discovery": bundle.provisioned.discovery,
                    "estimate": bundle.estimate.to_dict(),
                    "screen": render(bundle),
                    "language": bundle.compiled.org["pipeline"]["working_language"],
                }
            )
            results.append(row)
            print(f"{business['id']} #{generation}: {bundle.meta['template']}, running total {spent} tokens")
        if stopped:
            break
    summary = {"model": model, "max_tokens": max_tokens, "spent": spent, "stopped_at_cap": stopped, "results": results}
    (output / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"spent": spent, "stopped_at_cap": stopped, "generations": len(results)}))


if __name__ == "__main__":
    main(Path(sys.argv[1]), int(sys.argv[2]))
