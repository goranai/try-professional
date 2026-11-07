"""PAID: rerun only the generations the generator fixes affect, and stop at a hard limit.

usage: TRYPRO_EVAL_APPROVED=yes python run_confirm.py <output_dir> <max_tokens>

The limit is checked before every model call, so it can only be passed by one call. It saves after every generation,
and it needs psutil, like run_eval.py. Its order puts what each fix most needs first, so a limit costs the least.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from run_eval import BUSINESSES, OUTAGE_RETRIES, OUTAGE_WAIT_SECONDS, Counting, PeakRam, _row

from try_professional.adapters.api_runner.clients import ChatRequest, ChatResponse, ClientError
from try_professional.config import ModelConfig, build_client, load_env_file
from try_professional.generator import ArchitectFailed, create_proposal
from try_professional.memory import FastEmbedEmbedder
from try_professional.resources import LowMemory, free_memory_mb

# (business, full): full also searches GitHub and judges skills. A design alone costs far less.
RUNS = (
    ("saas-invoicing", False),  # It failed on "exactly one head".
    ("physio-clinic", True),  # Store skills for a clinic.
    ("pr-agency", False),  # Three runs, for whether the design now repeats.
    ("pr-agency", False),
    ("pr-agency", False),
    ("indonesian-education", True),  # Thin coverage, and the one business here that asks for a language.
    ("shorts", True),  # LinkedIn and Instagram skills for a YouTube company.
)


class Limited(Counting):
    def __init__(self, client: Any, limit: int) -> None:
        super().__init__(client)
        self.limit = limit

    def complete(self, request: ChatRequest) -> ChatResponse:
        if self.spent >= self.limit:
            raise ClientError(f"The limit of {self.limit} tokens is spent.", retryable=False)
        for attempt in range(OUTAGE_RETRIES):
            try:
                return super().complete(request)
            except ClientError as error:
                # An outage costs nothing, and mustn't look like a design failure.
                outage = "HTTP 5" in str(error) or "request failed" in str(error)
                if not outage or attempt == OUTAGE_RETRIES - 1:
                    raise
                print(f"The provider is down, so waiting {OUTAGE_WAIT_SECONDS}s.", flush=True)
                time.sleep(OUTAGE_WAIT_SECONDS)
        raise ClientError("The provider stayed down.")


def main(output: Path, limit: int) -> None:
    if os.environ.get("TRYPRO_EVAL_APPROVED") != "yes":
        raise SystemExit("This calls the paid API. Set TRYPRO_EVAL_APPROVED=yes once the owner has approved it.")
    load_env_file(Path.cwd())
    model = os.environ["TRYPRO_LLM_MODEL"]
    client = Limited(
        build_client(
            ModelConfig(
                provider="openai_compatible",
                tiers={},
                api_key_env="TRYPRO_LLM_API_KEY",
                base_url=os.environ["TRYPRO_LLM_BASE_URL"],
            )
        ),
        limit,
    )
    embedder = FastEmbedEmbedder()
    ram = PeakRam()
    known = {str(business["id"]): business for business in BUSINESSES}
    results: list[dict] = []
    stopped = ""

    def save() -> None:
        summary = {
            "model": model,
            "max_tokens": limit,
            "spent": {"input": client.input, "cached_input": client.cached, "output": client.output},
            "total": client.spent,
            "stopped": stopped,
            "results": results,
        }
        (output / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    output.mkdir(parents=True, exist_ok=True)
    for number, (business_id, full) in enumerate(RUNS):
        business = known[business_id]
        if client.spent >= limit:
            stopped = "The limit was reached before this run."
            break
        root = output / "work" / f"{number}-{business_id}"
        root.mkdir(parents=True, exist_ok=True)
        ram.take()
        free_before = free_memory_mb()
        try:
            bundle = create_proposal(
                root,
                embedder,
                description=str(business["description"]),
                company=None,
                client=client,
                model=model,
                discovery=full,
                provisioning=full,
                exclude_templates=tuple(business["exclude"]),
                say=print,
            )
        except LowMemory as error:
            stopped = str(error)
            break
        except ArchitectFailed as error:
            results.append({"business": business_id, "generation": number, "failed": str(error)})
            save()
            continue
        row = _row(business, number, bundle)
        row.update(
            full=full,
            notes=bundle.notes,
            off_trade=bundle.provisioned.off_trade,
            business_context=bundle.compiled.org["company"].get("business", {}),
            peak_ram_mb=ram.take(),
            free_ram_mb_before=free_before,
        )
        results.append(row)
        bundle.release_unpacked()
        save()
        print(f"{business_id} ({number}): running total {client.spent} tokens", flush=True)
    save()
    print(json.dumps({"total": client.spent, "stopped": stopped, "runs": len(results)}))


if __name__ == "__main__":
    main(Path(sys.argv[1]), int(sys.argv[2]))
