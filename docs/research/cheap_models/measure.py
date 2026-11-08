"""Real commands, through the company's own engine, on several models. Uses the API key in .env (paid, or not).

usage: python measure.py <out.json> <label> [models...]

Each model runs every goal once, in a new default company, with the shell and web on. It records whether the command
finished, its tokens and calls, and why it stopped if it did. Commands that would change something outside are refused,
because nobody is there to approve them.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from try_professional import Company
from try_professional.config import ModelConfig, build_client, load_env_file

GOALS = {
    "reply": "Write a two-sentence reply to a customer asking how long delivery takes. Facts: we deliver Monday to "
    "Friday, within 3 working days, and we don't deliver on weekends.",
    "plan": "Write a one-page plan for a bakery's first week on Instagram: five posts, with a caption for each.",
    "fix": "Fix this sentence for a printed menu, and change only what's wrong: 'Our cofee is roasted fresh evry day "
    "and served untill 5pm.'",
    "github": "Use the shell and gh to find one public GitHub repository with more than 1800 stars about Markdown "
    "editors. Report its name, its star count, and the first heading in its README. Don't open issues or PRs.",
    "indonesian": "Tulis balasan dua kalimat untuk pelanggan yang bertanya apakah kami buka hari Minggu. Fakta: kami "
    "buka Senin sampai Sabtu, pukul 08.00 sampai 17.00.",
}
DEFAULT_MODELS = ["deepseek-v4.1-flash-1017", "kimi-k3", "step-5-preview", "glm-5.3-Flash"]


def run_one(model: str, goal: str) -> dict[str, Any]:
    folder = Path(tempfile.mkdtemp(prefix="trypro-models-"))
    company = Company.create(folder / "co", name="Test Co")
    client = build_client(ModelConfig(provider="openai_compatible", tiers={}, api_key_env="TRYPRO_LLM_API_KEY",
                                      base_url=__import__("os").environ["TRYPRO_LLM_BASE_URL"]))  # fmt: skip
    started = time.time()
    try:
        result = company.run(goal, client=client, model=model)
    except Exception as error:  # A model that errors out is a result too.
        return {"status": "error", "reason": f"{type(error).__name__}: {str(error)[:300]}", "seconds": 0}
    status = company.status(result.run_id)
    report = company.final_report(result.run_id) or {}
    return {
        "status": result.status,
        "reason": (result.reason or "; ".join(status.questions))[:400],
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "calls": result.calls,
        "seconds": round(time.time() - started),
        "summary": str(report.get("summary_in_user_language", ""))[:600],
    }


def main(out: Path, label: str, models: list[str]) -> None:
    load_env_file(Path.cwd())
    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    rows = data.setdefault(label, {})
    for model in models:
        for name, goal in GOALS.items():
            key = f"{model}/{name}"
            if key in rows:
                continue
            rows[key] = run_one(model, goal)
            out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            row = rows[key]
            print(f"{key:40} {row['status']:8} {row.get('calls', '')} calls  {row.get('reason', '')[:90]}", flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3:] or DEFAULT_MODELS)
