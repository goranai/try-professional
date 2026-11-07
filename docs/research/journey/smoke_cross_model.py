"""PAID: a short test on another model: check-model, plus one small command. It waits out outages.

usage: TRYPRO_SMOKE_APPROVED=yes python smoke_cross_model.py <output.json>

It reads TRYPRO_LLM_BASE_URL, TRYPRO_LLM_API_KEY and TRYPRO_LLM_MODEL from .env. It says whether a model can run the
pipeline, and nothing about how good its work is, which the journey on Claude Code doesn't show either.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from try_professional.adapters.api_runner.clients import ChatRequest, ChatResponse, ClientError
from try_professional.adapters.api_runner.runner import ApiRunner, ModelRoutes
from try_professional.check_model import ModelUnreachable, check_model
from try_professional.config import ModelConfig, build_client, load_env_file
from try_professional.core.audit import read_records
from try_professional.core.orchestrator import Orchestrator
from try_professional.org import load_org
from try_professional.packs import available_packs, install_org

COMMAND = "Write a two-sentence welcome note for a new newsletter subscriber."
OUTAGE_WAIT_SECONDS = 180
OUTAGE_RETRIES = 40
COMMAND_TOKEN_LIMIT = 200_000


class Waiting:
    """Waits out an outage, because one says nothing about a model."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.input = 0
        self.cached = 0
        self.output = 0
        self.waits = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def complete(self, request: ChatRequest) -> ChatResponse:
        for attempt in range(OUTAGE_RETRIES):
            try:
                response: ChatResponse = self._client.complete(request)
            except ClientError as error:
                outage = "HTTP 5" in str(error) or "request failed" in str(error) or "HTTP 429" in str(error)
                if not outage or attempt == OUTAGE_RETRIES - 1:
                    raise
                self.waits += 1
                print(f"The provider is down, so waiting {OUTAGE_WAIT_SECONDS}s.", flush=True)
                time.sleep(OUTAGE_WAIT_SECONDS)
            else:
                self.input += response.input_tokens
                self.cached += response.cached_input_tokens
                self.output += response.output_tokens
                return response
        raise ClientError("The provider stayed down.", retryable=False)


def main(output: Path) -> None:
    if os.environ.get("TRYPRO_SMOKE_APPROVED") != "yes":
        raise SystemExit("This calls the paid API. Set TRYPRO_SMOKE_APPROVED=yes once the owner has approved it.")
    load_env_file(Path.cwd())
    model = os.environ["TRYPRO_LLM_MODEL"]
    client = Waiting(
        build_client(
            ModelConfig(
                provider="openai_compatible",
                tiers={},
                api_key_env="TRYPRO_LLM_API_KEY",
                base_url=os.environ["TRYPRO_LLM_BASE_URL"],
            )
        )
    )
    result: dict[str, Any] = {"model": model}
    try:
        result["check_model"] = check_model(client, model).to_dict()
    except ModelUnreachable as error:
        result["check_model"] = {"unreachable": str(error)[:200]}
    with tempfile.TemporaryDirectory(prefix="trypro-smoke-", ignore_cleanup_errors=True) as work:
        root = Path(work)
        install_org(root / "org", "Smoke", available_packs())
        org = load_org(root / "org")
        company = Orchestrator(org, root / "home", deliverables_dir=root / "deliverables")
        routes = ModelRoutes({"standard": model, "executive": model, "fast": model})
        run_id, _ = company.start(COMMAND)
        state = company.state(run_id)
        state.budget_override = COMMAND_TOKEN_LIMIT
        company.ledger.save(state, company._log(run_id).head, company.clock())
        run = ApiRunner(company, client, routes).resume(run_id)
        state = company.state(run_id)
        result["small_command"] = {
            "goal": COMMAND,
            "status": run.status,
            "size": state.size,
            "final": run.final,
            "counters": state.counters.model_dump(),
            "questions": [r.question[:300] for r in state.human_requests.values()],
            "audit_ok": company.verify_audit(run_id).ok,
            # Why work was sent back, which is what shows whether a model suits this.
            "rejections": [
                str(record["data"].get("reason"))[:300]
                for record in read_records(company.audit_path(run_id))
                if record["kind"] in ("invalid_output", "rejected")
            ],
            "review_issues": [
                issue["description"][:300]
                for artifact in state.artifacts.values()
                if artifact.type == "ReviewVerdict"
                for issue in artifact.payload.get("issues", [])
            ],
        }
        company.close()
    result["tokens"] = {"input": client.input, "cached_input": client.cached, "output": client.output}
    result["outage_waits"] = client.waits
    output.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps({k: result[k] for k in ("model", "tokens", "outage_waits")}))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
