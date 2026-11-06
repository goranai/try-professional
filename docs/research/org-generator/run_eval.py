"""PAID: generate each business's company three times, and record everything for a person to judge.

usage: TRYPRO_EVAL_APPROVED=yes python run_eval.py <output_dir> <max_tokens> [already_spent]

It stops before starting a generation once it has spent max_tokens, and marks the results as stopped. It
saves after every generation. Run again to continue: finished proposals are read back, for free, and
already_spent counts what an earlier attempt paid for. A provider outage is waited out, since it costs nothing.
Only the first generation of each business finds skills and pays for the re-rank, as the owner chose,
and the other two show whether the design is consistent. Close templates are held out for the businesses
meant to have none, so those really do start from further away.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from try_professional.adapters.api_runner.clients import ChatRequest, ChatResponse
from try_professional.config import ModelConfig, build_client, load_env_file
from try_professional.generator import ArchitectFailed, create_proposal, load_proposal, render
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


OUTAGE_WAIT_SECONDS = 180
OUTAGE_RETRIES = 20


class Counting:
    """Counts every call, including those in a generation that fails, so the limit sees all of it."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.input = 0
        self.cached = 0
        self.output = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    @property
    def spent(self) -> int:
        return self.input + self.output

    def complete(self, request: ChatRequest) -> ChatResponse:
        response: ChatResponse = self._client.complete(request)
        self.input += response.input_tokens
        self.cached += response.cached_input_tokens
        self.output += response.output_tokens
        return response


def _row(business: dict, generation: int, bundle: Any) -> dict:
    tokens = bundle.meta["tokens"]
    return {
        "business": business["id"],
        "description": business["description"],
        "generation": generation,
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
                        "title": bundle.org.role(f"{d.id}/{r}").display_name,
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


def _finished(root: Path) -> list[Any]:
    """Proposals an earlier attempt finished, with the one that found skills first."""
    bundles = []
    for path in (root / ".trypro" / "proposals").glob("prop-*"):
        if (path / "meta.json").is_file():
            bundles.append(load_proposal(root, path.name))
    bundles.sort(key=lambda b: (b.meta["tokens"]["rerank"]["calls"] == 0, b.path.stat().st_mtime))
    return bundles


def _generate(business: dict, root: Path, embedder: Any, client: Any, model: str, generation: int) -> Any:
    for _ in range(OUTAGE_RETRIES):
        try:
            return create_proposal(
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
            if "HTTP 5" not in str(error) and "request failed" not in str(error):
                raise
            print(f"The provider is down, so waiting {OUTAGE_WAIT_SECONDS}s: {str(error)[:80]}", flush=True)
            time.sleep(OUTAGE_WAIT_SECONDS)
    raise ArchitectFailed("The provider stayed down.")


def main(output: Path, max_tokens: int, already_spent: int = 0) -> None:
    if os.environ.get("TRYPRO_EVAL_APPROVED") != "yes":
        raise SystemExit("This calls the paid API. Set TRYPRO_EVAL_APPROVED=yes once the owner has approved it.")
    load_env_file(Path.cwd())
    model = os.environ["TRYPRO_LLM_MODEL"]
    client = Counting(
        build_client(
            ModelConfig(
                provider="openai_compatible",
                tiers={},
                api_key_env="TRYPRO_LLM_API_KEY",
                base_url=os.environ["TRYPRO_LLM_BASE_URL"],
            )
        )
    )
    embedder = FastEmbedEmbedder()
    output.mkdir(parents=True, exist_ok=True)
    stopped = False
    results: list[dict] = []

    def save() -> None:
        spent = already_spent + client.spent
        summary = {
            "model": model,
            "max_tokens": max_tokens,
            "spent": spent,
            "this_attempt": {"input": client.input, "cached_input": client.cached, "output": client.output},
            "stopped_at_cap": stopped,
            "results": results,
        }
        (output / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    for business in BUSINESSES:
        root = output / "work" / str(business["id"])
        root.mkdir(parents=True, exist_ok=True)
        finished = _finished(root)
        has_first = bool(finished) and finished[0].meta["tokens"]["rerank"]["calls"] > 0
        for generation in range(GENERATIONS):
            if finished and (generation > 0 or has_first):
                results.append(_row(business, generation, finished.pop(0)))
                save()
                continue
            if already_spent + client.spent >= max_tokens:
                stopped = True
                break
            try:
                bundle = _generate(business, root, embedder, client, model, generation)
                results.append(_row(business, generation, bundle))
            except ArchitectFailed as error:
                results.append(
                    {
                        "business": business["id"],
                        "description": business["description"],
                        "generation": generation,
                        "failed": str(error),
                    }
                )
            save()
            print(f"{business['id']} #{generation}: running total {already_spent + client.spent} tokens", flush=True)
        if stopped:
            break
    save()
    print(json.dumps({"spent": already_spent + client.spent, "stopped_at_cap": stopped, "generations": len(results)}))


if __name__ == "__main__":
    main(Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 0)
