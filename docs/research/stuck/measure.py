"""How far a stuck loop gets before the run stops, on the scripted model, with real token sizes.

It plays the stuck run from an external test of 0.1.2: a small command whose writer kept reading and rewriting its
workspace, now and then handing in work that was refused, and never got anything accepted. Each model call reports the
tokens that run's calls reported, in order. When the run asks about its budget, the script resumes it with a limit of
250,000, as the tester did. It stops at the first question that isn't a budget question, and says what it was.

Run it with whichever version of try-professional is installed:

    python docs/research/stuck/measure.py <label> results.json
"""

from __future__ import annotations

import json
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path
from typing import Any

from try_professional.adapters.api_runner.clients import ChatRequest, ChatResponse, ToolCall
from try_professional.adapters.api_runner.runner import ApiRunner, ModelRoutes
from try_professional.adapters.api_runner.scripted import ScriptedClient, default_payload
from try_professional.adapters.api_runner.tools import SUBMIT
from try_professional.core.orchestrator import Orchestrator
from try_professional.core.pipeline import HumanDecided, Purpose
from try_professional.org import load_org
from try_professional.packs import available_packs, install_org

ROUTES = ModelRoutes({"standard": "m", "executive": "m", "fast": "m"})
SMALL = {"size": "small", "route_department": "marketing", "route_role": "marketing/content-writer"}
# Input, cached input, and output of each call by the stuck writer, from the external run's audit log.
PROFILE = [
    (3878, 0, 75),
    (4415, 3840, 75),
    (5964, 4352, 221),
    (6207, 5888, 539),
    (6769, 6144, 505),
    (7295, 6656, 595),
    (7908, 7168, 1953),
    (4065, 0, 75),
    (4689, 3840, 182),
    (7508, 4608, 174),
    (7699, 7424, 613),
    (8394, 7680, 2954),
    (11424, 8192, 2968),
]
RESUMED_LIMIT = 250_000

# The external run itself, call by call: input, cached input, output, and what the call did. "tool" read or wrote
# the workspace, "invalid" handed in something malformed, "submit" handed in valid work or a verdict, and "unreported"
# handed in work that left out part of its brief. Numbers only, from its audit log, by role, in order.
REPLAY = {
    "execute": [
        (2245, 0, 637, "tool"), (2905, 2048, 1508, "invalid"), (4448, 2816, 1594, "submit"),
        (2641, 0, 60, "tool"), (3251, 2560, 47, "tool"), (3321, 3072, 748, "tool"), (4092, 3072, 49, "tool"),
        (4836, 3840, 196, "tool"), (5049, 4608, 929, "tool"), (6001, 4864, 69, "tool"), (6700, 5888, 925, "tool"),
        (2647, 0, 93, "tool"), (3478, 2560, 49, "tool"), (4137, 3328, 516, "tool"), (4676, 4096, 1311, "invalid"),
        (6029, 4608, 2037, "submit"),
        (3178, 2304, 93, "tool"), (3862, 3072, 23, "tool"), (3916, 3840, 481, "tool"), (4420, 3840, 1392, "tool"),
        (5829, 4352, 2420, "invalid"), (8284, 5632, 2450, "submit"),
        (3878, 0, 75, "tool"), (4415, 3840, 75, "tool"), (5964, 4352, 221, "tool"), (6207, 5888, 539, "tool"),
        (6769, 6144, 505, "tool"), (7295, 6656, 595, "tool"), (7908, 7168, 1953, "unreported"),
        (4065, 0, 75, "tool"), (4689, 3840, 182, "tool"), (7508, 4608, 174, "tool"), (7699, 7424, 613, "tool"),
        (8394, 7680, 2954, "invalid"), (11424, 8192, 2968, "submit"),
    ],
    "review": [
        (2717, 0, 60, "tool"), (3327, 2560, 579, "submit"),
        (3186, 0, 96, "tool"), (3873, 3072, 718, "invalid"), (4635, 3840, 653, "submit"),
        (3388, 0, 140, "tool"), (5427, 3328, 43, "tool"), (5601, 5376, 1038, "submit"),
    ],
    "escalation": [(1819, 0, 103, "tool"), (1965, 1792, 47, "tool"), (2058, 1792, 88, "tool"), (2189, 2048, 1401, "submit")],
}  # fmt: skip


class Writer(ScriptedClient):
    """Every role behaves, except the writer, which loops in the way `kind` says."""

    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind
        self.turn = 0
        self.invocations: list[str] = []
        self.started = 0
        self.played = {purpose: 0 for purpose in REPLAY}

    def complete(self, request: ChatRequest) -> ChatResponse:
        invocation, state, org = request.metadata["invocation"], request.metadata["state"], request.metadata["org"]
        if invocation.purpose == Purpose.INTAKE:
            self.calls.append({"role": invocation.role})
            payload = {**default_payload(invocation, state, org), **SMALL}
            return self._reply([ToolCall(id="i", name=SUBMIT, arguments={"payload": payload})], (1220, 0, 389))
        if self.kind == "the external run, replayed":
            return self._replay(invocation, state, org)
        if invocation.purpose == Purpose.REVIEW and self.kind == "sent back, then loops":
            # The reviewer is right to send it back: the work never meets the brief.
            self.calls.append({"role": invocation.role})
            issues = [{"severity": "major", "description": "It doesn't report on every item in the brief"}]
            verdict = {**default_payload(invocation, state, org), "decision": "changes_requested", "issues": issues}
            return self._reply([ToolCall(id="r", name=SUBMIT, arguments={"payload": verdict})], (5427, 3328, 1038))
        if invocation.purpose != Purpose.EXECUTE:
            return super().complete(request)
        self.calls.append({"role": invocation.role})
        tokens = PROFILE[self.turn % len(PROFILE)]
        self.turn += 1
        if invocation.id not in self.invocations:
            self.invocations.append(invocation.id)
            self.started = self.turn
        if self.kind == "sent back, then loops" and len(self.invocations) <= 3 and self.turn - self.started == 6:
            # Its first attempts get handed in after 7 calls, and sent back. After that it loops.
            payload = default_payload(invocation, state, org)
            return self._reply([ToolCall(id="s", name=SUBMIT, arguments={"payload": payload})], tokens)
        if self.kind == "slow but fine" and self.turn % 6 == 0:
            payload = default_payload(invocation, state, org)
            return self._reply([ToolCall(id="s", name=SUBMIT, arguments={"payload": payload})], tokens)
        if self.kind == "refused hand-ins" and self.turn % 2 == 0:
            # Handed in, and not valid: a schema error the runner sends straight back.
            return self._reply([ToolCall(id="s", name=SUBMIT, arguments={"payload": {"extra": True}})], tokens)
        write = {"path": "draft.md", "content": f"Draft {self.turn}"}
        return self._reply([ToolCall(id="w", name="workspace_write", arguments=write)], tokens)

    def _replay(self, invocation: Any, state: Any, org: Any) -> ChatResponse:
        purpose = invocation.purpose.value
        calls = REPLAY.get(purpose, [])
        index = self.played.get(purpose, 0)
        self.played[purpose] = index + 1
        self.calls.append({"role": invocation.role})
        # Past the end of the record, it carries on as its last stretch did: reading and writing, never handing in.
        tokens_in, cached, tokens_out, does = calls[index] if index < len(calls) else (*calls[-7][:3], "tool")
        tokens = (tokens_in, cached, tokens_out)
        payload = default_payload(invocation, state, org)
        if does == "tool":
            write = {"path": "draft.md", "content": f"Draft {index}"}
            return self._reply([ToolCall(id="w", name="workspace_write", arguments=write)], tokens)
        if does == "invalid":
            return self._reply([ToolCall(id="s", name=SUBMIT, arguments={"payload": {"extra": True}})], tokens)
        if does == "unreported":
            payload = {**payload, "done_criteria_met": []}
        if invocation.purpose == Purpose.REVIEW:
            issues = [{"severity": "major", "description": "It doesn't report on every item in the brief"}]
            payload = {**payload, "decision": "changes_requested", "issues": issues}
        return self._reply([ToolCall(id="s", name=SUBMIT, arguments={"payload": payload})], tokens)

    @staticmethod
    def _reply(calls: list[ToolCall], tokens: tuple[int, int, int]) -> ChatResponse:
        return ChatResponse(
            text="",
            tool_calls=calls,
            stop_reason="tool_use",
            assistant_message={"role": "assistant", "content": ""},
            input_tokens=tokens[0],
            cached_input_tokens=tokens[1],
            output_tokens=tokens[2],
            model="scripted",
        )


def measure(kind: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        install_org(root / "org", "Test", available_packs())
        company = Orchestrator(load_org(root / "org"), root / "home")
        asked: list[dict[str, Any]] = []
        run_id = ""

        def answer(question: Any) -> Any:
            state = company.state(run_id)
            request = state.human_requests[question.request_id]
            asked.append(
                {
                    "kind": request.kind,
                    "tokens": state.usage.total,
                    "calls": state.usage.calls,
                    "calls_since_progress": state.calls_since_progress,
                    "rework_rounds": state.counters.rework_loops,
                    "escalations": state.counters.escalations,
                }
            )
            if request.kind == "budget" and sum(a["kind"] == "budget" for a in asked) == 1:
                return HumanDecided(request_id=request.id, decision="resume", new_token_budget=RESUMED_LIMIT)
            return None

        try:
            runner = ApiRunner(company, Writer(kind), ROUTES, on_human=answer)
            run_id, _ = company.start("Write a one-page checklist, under 400 words.")
            result = runner.resume(run_id)
            state = company.state(run_id)
            return {
                "kind": kind,
                "status": result.status,
                "stopped_by": asked[-1]["kind"] if asked and result.status == "blocked" else None,
                "questions": asked,
                "tokens": state.usage.total,
                "calls": state.usage.calls,
            }
        finally:
            company.close()


def main(label: str, out: Path) -> None:
    results = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    rows = [
        measure(kind)
        for kind in (
            "the external run, replayed",
            "never hands in",
            "refused hand-ins",
            "sent back, then loops",
            "slow but fine",
        )
    ]
    results[label] = {"try_professional": version("try-professional"), "runs": rows}
    out.write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    for row in rows:
        print(
            f"{label}: {row['kind']}: {row['status']}, {row['stopped_by']}, {row['tokens']:,} tokens, {row['calls']} calls"
        )


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]).resolve())
