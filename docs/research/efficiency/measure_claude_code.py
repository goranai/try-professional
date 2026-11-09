"""The same seven jobs through Claude Code, the way a person uses it: `/company <job>`, every role on one model.

usage: python measure_claude_code.py <out.json> <label> [model]

Each job gets a new default company inside this repository, so Claude Code trusts the folder, with its roles written
for `model` (default haiku), and the session on the same model. Roles may use the shell and the web without asking,
because nobody is there to; the company's deny rules still stop anything that changes something outside, such as
`gh issue create` or `git push`. It records Claude Code's own account of tokens and cost, and keeps the company, so
summarize.py can measure the run from its audit log. A usage limit is waited out, never worked around.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure import GOALS  # noqa: E402

ALLOWED = "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch"
RUN_SECONDS = 2400
LIMIT = re.compile(r"hit your (\S+ )?limit|usage limit|resets? (at )?\d", re.IGNORECASE)
LIMIT_WAIT_SECONDS = 900


def _claude() -> str:
    found = shutil.which("claude") or shutil.which("claude.cmd")
    if not found:
        raise SystemExit("Claude Code isn't installed here.")
    return found


def _trypro(folder: Path, *args: str) -> None:
    """With retries: on a busy machine, Windows sometimes fails to start a process (0xC0000142), which isn't ours."""
    for attempt in range(4):
        try:
            _trypro_once(folder, *args)
            return
        except subprocess.CalledProcessError:
            if attempt == 3:
                raise
            time.sleep(30 * (attempt + 1))


def _trypro_once(folder: Path, *args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "try_professional.cli", *args],
        cwd=folder,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _stop_embedding(folder: Path) -> None:
    """Each company here is used once. Its shared embedding service would otherwise wait 10 minutes to exit, holding
    about 600 MB, and a few of them stacked up are enough to run a machine out of memory."""
    address = folder / ".trypro" / "embed-service.json"
    try:
        pid = int(json.loads(address.read_text(encoding="utf-8"))["pid"])
    except (OSError, ValueError, KeyError):
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
    else:
        import signal

        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)


def _read_stream(text: str) -> dict[str, Any]:
    """Claude Code's own account of a session. It writes a result each time a background subagent reports back; the
    per-model usage in the last one covers the whole session, subagents included, with its list-price cost."""
    results = []
    for line in text.splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if message.get("type") == "result":
            results.append(message)
    final = results[-1] if results else {}
    models = (final.get("modelUsage") or {}).values()
    total = {
        key: sum(int(m.get(key) or 0) for m in models)
        for key in ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
    }
    return {
        "cc_cost_usd": round(sum(float(m.get("costUSD") or 0) for m in models), 4),
        "cc_input": total["inputTokens"],
        "cc_cache_write": total["cacheCreationInputTokens"],
        "cc_cache_read": total["cacheReadInputTokens"],
        "cc_output": total["outputTokens"],
        "cc_results": len(results),
        "said": str(final.get("result") or "")[-600:],
    }


def run_one(label: str, name: str, goal: str, model: str) -> dict[str, Any]:
    from try_professional import Company

    folder = REPO / ".trypro" / "bench-cc" / label / name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    _trypro(folder, "init", "--company", "Test Co", "--no-discover", "--skip-model")
    _trypro(folder, "agents", "claude-code", "--model", model)
    environment = {k: v for k, v in os.environ.items() if not k.startswith("TRYPRO_LLM_")}
    environment.update(MCP_TOOL_TIMEOUT="900000", MCP_TIMEOUT="120000")
    command = [
        _claude(),
        "-p",
        f"/company {goal}",
        "--model",
        model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--allowedTools",
        ALLOWED,
    ]
    started = time.time()
    while True:
        try:
            done = subprocess.run(
                command,
                cwd=folder,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=RUN_SECONDS,
                stdin=subprocess.DEVNULL,
            )
            stream, stopped = done.stdout, ""
        except subprocess.TimeoutExpired as error:
            stream, stopped = str(error.stdout or ""), f"stopped after {RUN_SECONDS} s"
        (folder / "claude-code.jsonl").write_text(stream, encoding="utf-8")
        account = _read_stream(stream)
        if LIMIT.search(account["said"]) and not stopped:
            print(f"usage limit: waiting {LIMIT_WAIT_SECONDS // 60} min", flush=True)
            time.sleep(LIMIT_WAIT_SECONDS)
            shutil.rmtree(folder / ".trypro", ignore_errors=True)
            continue
        break
    _stop_embedding(folder)
    company = Company.open(folder)
    runs = company.runs()
    status = company.status(runs[0]).phase if runs else "not started"
    return {
        "status": status,
        "run_id": runs[0] if runs else "",
        "reason": stopped,
        "seconds": round(time.time() - started),
        "folder": str(folder),
        **account,
    }


def main(out: Path, label: str, model: str) -> None:
    from try_professional import __version__

    data = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    rows = data.setdefault(label, {"version": __version__, "model": model, "engine": "claude-code", "runs": {}})["runs"]
    for name, goal in GOALS.items():
        if name in rows:
            continue
        row = run_one(label, name, goal, model)
        rows[name] = row
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(
            f"{label} {name:14} {row['status']:8} {row['seconds']}s ${row.get('cc_cost_usd') or 0:.2f} {row['reason']}",
            flush=True,
        )


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "haiku")
