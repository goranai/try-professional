"""A step's model usage comes from its CLI, never from company tool traffic."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from try_professional.adapters.agent_runner import AgentRunner, AgentSettings, RuntimePlan, tokens_used
from try_professional.adapters.cli_usage import CliUsageTracker, cli_usage
from try_professional.adapters.mcp import McpService
from try_professional.cli import _print_result, _status_tokens, main
from try_professional.core.audit import read_records
from try_professional.core.company import _MIGRATIONS, CompanyStore
from try_professional.core.efficiency import measure
from try_professional.core.orchestrator import Orchestrator
from try_professional.core.pipeline import UsageRecorded
from try_professional.core.pipeline.state import ESTIMATED_BY_SERVER, Usage
from try_professional.org import Org

FIXTURES = Path(__file__).parent / "fixtures" / "cli_usage"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "runtime,name,raw,cached,created,inputs,outputs,inclusive",
    [
        ("codex", "codex-turn.jsonl", 1000, 980, 10, 1000, 20, True),
        ("antigravity", "antigravity-resumed.json", 15930, 11566, 0, 27496, 92, False),
        ("claude-code", "claude-result.json", 23, 970, 10, 1003, 7, False),
    ],
)
def test_each_cli_preserves_reported_counts_and_counts_cache_once(
    runtime: str, name: str, raw: int, cached: int, created: int, inputs: int, outputs: int, inclusive: bool
) -> None:
    usage = cli_usage(runtime, "startup diagnostic\n" + _fixture(name) + "\nstderr diagnostic\n")
    assert usage.status == "exact"
    assert (usage.input_tokens, usage.cached_input_tokens, usage.output_tokens) == (inputs, cached, outputs)
    assert (usage.raw_input_tokens, usage.cache_creation_input_tokens) == (raw, created)
    assert usage.raw_input_tokens_includes_cache is inclusive
    assert usage.source.startswith(runtime + ":")
    assert tokens_used(runtime, _fixture(name)) == inputs + outputs


@pytest.mark.parametrize(
    "runtime,output",
    [
        ("codex", "tokens used\n11,080\n"),
        ("antigravity", '{"conversation_id":"x","status":"SUCCESS","usage":{"total_tokens":13263}}'),
        ("claude-code", '{"type":"result","usage":{"input_tokens":100,"output_tokens":20}}'),
        ("codex", '{"type":"turn.completed","usage":null}'),
        ("codex", '{"type":"turn.completed","usage":'),
        ("codex", '[{"type":"item.completed","usage":{"input_tokens":1}}]'),
        ("claude-code", "[]\nnull\n42\n{}"),
    ],
)
def test_missing_or_partial_usage_does_not_become_an_exact_total(runtime: str, output: str) -> None:
    assert cli_usage(runtime, output).status != "exact"
    assert tokens_used(runtime, output) is None


@pytest.mark.parametrize("bad", [None, -1, True, "1000", 1.5])
def test_invalid_counters_are_unverified_without_crashing(bad: Any) -> None:
    result = json.loads(_fixture("claude-result.json"))
    result["usage"]["input_tokens"] = bad
    usage = cli_usage("claude-code", json.dumps(result))
    assert usage.status == "unverified" and usage.total is None


def test_zero_counts_are_available_and_reasoning_is_not_added_again() -> None:
    result = json.loads(_fixture("claude-result.json"))
    result["usage"].update(input_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=0)
    result["modelUsage"]["claude-sonnet-4-6"].update(
        inputTokens=0, cacheReadInputTokens=0, cacheCreationInputTokens=0, outputTokens=0
    )
    assert tokens_used("claude-code", json.dumps([{"type": "system"}, result])) == 0
    assert tokens_used("codex", _fixture("codex-turn.jsonl")) == 1020


@pytest.mark.parametrize("name,runtime", [("claude-error.json", "claude-code"), ("codex-default.jsonl", "codex")])
def test_default_cli_counters_are_preserved_as_evidence_without_claiming_zero_usage(name: str, runtime: str) -> None:
    usage = cli_usage(runtime, _fixture(name))
    assert usage.status in ("unavailable", "unverified") and usage.total is None
    assert usage.reported[-1]["usage"]["input_tokens"] == 0


@pytest.mark.parametrize("error", [True, False])
def test_claude_counters_require_a_matching_model_breakdown(error: bool) -> None:
    result = json.loads(_fixture("claude-result.json"))
    result["is_error"] = error
    for models in [{}, {"claude-sonnet-4-6": {"inputTokens": 99}}, {"claude-sonnet-4-6": None}]:
        result["modelUsage"] = models
        usage = cli_usage("claude-code", json.dumps(result))
        assert usage.status == "unverified" and usage.input_tokens == 0


def test_a_failed_claude_result_retains_measured_counts_as_partial() -> None:
    result = json.loads(_fixture("claude-result.json"))
    result.update(is_error=True, terminal_reason="api_error")
    usage = cli_usage("claude-code", json.dumps(result), exit_code=1)
    assert usage.status == "partial" and usage.input_tokens == 1003 and usage.total is None


@pytest.mark.parametrize(
    "runtime,name,bad",
    [
        ("codex", "codex-turn.jsonl", "codex-default.jsonl"),
        ("claude-code", "claude-result.json", "claude-error.json"),
    ],
)
def test_later_default_results_keep_earlier_verified_counts_as_partial(runtime: str, name: str, bad: str) -> None:
    usage = cli_usage(runtime, _fixture(name) + "\n" + _fixture(bad), exit_code=1)
    assert usage.status == "partial" and usage.input_tokens > 0 and usage.total is None
    assert len(usage.reported) == 2


@pytest.mark.parametrize(
    "runtime,name,exit_code,status,total",
    [
        ("claude-code", "claude-error.json", 1, "unavailable", 0),
        ("codex", "codex-default.jsonl", 0, "unavailable", 0),
        ("codex", "codex-default.jsonl", 1, "unavailable", 0),
        ("codex", "codex-turn.jsonl", 1, "partial", 1020),
        ("claude-code", "claude-result.json", 0, "exact", 0),
    ],
)
def test_failed_or_default_cli_results_reach_step_auditing_and_public_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    runtime: str,
    name: str,
    exit_code: int,
    status: str,
    total: int,
) -> None:
    from try_professional.adapters import agent_runner
    from try_professional.config import load_config
    from try_professional.org import load_org

    monkeypatch.chdir(tmp_path)
    assert main(["init", "--company", "Test", "--skip-model"]) == 0
    config = load_config(tmp_path)
    orchestrator = Orchestrator(load_org(config.org_dir), config.home)
    run_id, _ = orchestrator.start("Goal")
    action = orchestrator.open_actions(run_id)[0]
    printed = _fixture(name)
    if status == "exact":
        zero = json.loads(printed)
        zero["usage"].update(input_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=0)
        zero["modelUsage"]["claude-sonnet-4-6"].update(
            inputTokens=0, cacheReadInputTokens=0, cacheCreationInputTokens=0, outputTokens=0
        )
        printed = json.dumps(zero)

    def captured(*args: Any, **kwargs: Any) -> agent_runner.Step:
        folder = args[7]
        folder.mkdir(parents=True, exist_ok=True)
        code = f"import sys; sys.stdout.write({printed!r}); sys.exit({exit_code})"
        return agent_runner.Step([sys.executable, "-c", code], "", folder)

    monkeypatch.setattr(agent_runner, "build_step", captured)
    try:
        # The review reproduced this through the default process runner and gate,
        # so replay the captured stdout through an actual child process too.
        runner = AgentRunner(
            orchestrator,
            RuntimePlan(default=runtime),
            AgentSettings(gate=True),
            root=tmp_path,
            environ=dict(os.environ),
        )
        runner._invoke(action, run_id)
        records = read_records(orchestrator.audit_path(run_id))
        step = next(r["data"] for r in records if r["kind"] == "agent_step")
        assert step["exit"] == exit_code
        assert step["tokens_used"] == (total if status == "exact" else None)
        assert (
            step["usage_status"] == status and step["usage"]["input_tokens"] + step["usage"]["output_tokens"] == total
        )
        evidence = next(r["data"] for r in records if r["kind"] == "cli_usage_evidence")
        assert (
            evidence["reported"][-1]["usage"]
            == json.loads(printed.splitlines()[-1] if runtime == "codex" else printed)["usage"]
        )
        assert McpService(orchestrator).run_status(run_id)["model_usage"]["status"] == status
    finally:
        orchestrator.close()
    capsys.readouterr()
    assert main(["status", run_id]) == 0
    output = capsys.readouterr().out
    assert f"Model tokens ({status}): {total}" in output
    if status != "exact":
        assert "Model tokens (exact)" not in output and "unavailable counts are not zero usage" in output


def test_codex_repeated_cumulative_snapshots_are_not_summed() -> None:
    printed = _fixture("codex-turn.jsonl")
    assert tokens_used("codex", printed + printed.splitlines()[-1]) == 1020


def test_a_failed_codex_turn_keeps_prior_counts_partial_even_without_a_process_error() -> None:
    usage = cli_usage("codex", _fixture("codex-turn.jsonl") + '\n{"type":"turn.failed","error":{"message":"failed"}}')
    assert usage.status == "partial" and usage.input_tokens == 1000 and usage.total is None


def test_antigravity_continuations_charge_only_the_conversation_delta() -> None:
    tracker = CliUsageTracker()
    first = tracker.read("antigravity", _fixture("antigravity-first.json"))
    second = tracker.read("antigravity", _fixture("antigravity-resumed.json"))
    assert (first.input_tokens, first.output_tokens) == (13615, 52)
    assert (second.raw_input_tokens, second.cached_input_tokens, second.input_tokens, second.output_tokens) == (
        2315,
        11566,
        13881,
        40,
    )
    assert first.total + second.total == 27588
    assert tracker.read("antigravity", _fixture("antigravity-resumed.json")).total == 0
    assert tracker.read("antigravity", _fixture("antigravity-first.json")).status == "unverified"
    fresh = json.loads(_fixture("antigravity-first.json"))
    fresh["conversation_id"] = "new-session"
    assert tracker.read("antigravity", json.dumps(fresh)).total == 13667


def _runner(run: Any, runtime: str, process: Callable[..., Any]) -> AgentRunner:
    return AgentRunner(
        run.orchestrator,
        RuntimePlan(default=runtime),
        AgentSettings(codex="codex", antigravity="agy", claude="claude", gate=False),
        root=run.orchestrator.home,
        run_process=process,
        environ={"PATH": ""},
        sleep=lambda _: None,
    )


@pytest.mark.parametrize(
    "runtime,name,inputs,cached,outputs",
    [
        ("codex", "codex-turn.jsonl", 1000, 980, 20),
        ("antigravity", "antigravity-resumed.json", 27496, 11566, 92),
        ("claude-code", "claude-result.json", 1003, 970, 7),
    ],
)
def test_cli_model_counts_reach_step_run_and_role_usage_separately_from_tool_estimates(
    org: Org, harness: Callable[..., Any], runtime: str, name: str, inputs: int, cached: int, outputs: int
) -> None:
    run = harness(org)
    run_id, _ = run.orchestrator.start("Goal")
    action = run.orchestrator.open_actions(run_id)[0]
    service = McpService(run.orchestrator)
    service._charge(run_id, action.invocation_id, action.role, "x" * 10000, "y" * 10000)
    before = run.orchestrator.state(run_id).usage.estimated_tool_tokens
    runner = _runner(run, runtime, lambda *_: (0, _fixture(name)))
    runner._invoke(action, run_id)
    state = run.orchestrator.state(run_id)
    assert (state.usage.input_tokens, state.usage.cached_input_tokens, state.usage.output_tokens) == (
        inputs,
        cached,
        outputs,
    )
    assert state.usage.total == inputs + outputs and state.usage.estimated_tool_tokens == before
    assert state.usage.model_usage_status == "exact" and not state.usage.estimated
    for bucket in [
        state.usage_by_role[action.role],
        state.usage_by_purpose[action.purpose.value],
        state.usage_by_invocation[action.invocation_id],
    ]:
        assert bucket.total == inputs + outputs and bucket.estimated_tool_tokens == before
    records = read_records(run.orchestrator.audit_path(run_id))
    step = next(r["data"] for r in records if r["kind"] == "agent_step")
    assert step["tokens_used"] == inputs + outputs and step["usage_status"] == "exact"
    events = [r["data"] for r in records if r["data"].get("kind") == "usage_recorded"]
    assert events[-1]["usage_source"].startswith(runtime + ":")
    assert events[-1]["calls"] is None
    status = service.run_status(run_id)
    assert status["model_usage"]["total_tokens"] == inputs + outputs
    assert status["estimated_tool_traffic"]["total_tokens"] == before
    assert status["tokens_estimated_by_server"] == before
    measured = measure(run.orchestrator.audit_path(run_id), run_id)
    assert measured.tokens == inputs + outputs and measured.estimated_tool_tokens == before
    assert measured.calls == 0 and measured.unknown_model_calls == 1
    stored = run.orchestrator.company.tokens_by_command()[0]
    assert stored["input"] == inputs and stored["cached_input"] == cached
    assert stored["estimated_tool_tokens"] == before and stored["model_usage_status"] == "exact"


def test_usage_survives_submission_before_the_cli_prints_its_result(org: Org, harness: Callable[..., Any]) -> None:
    run = harness(org)
    run_id, _ = run.orchestrator.start("Goal")
    action = run.orchestrator.open_actions(run_id)[0]

    def process(*_: Any) -> tuple[int, str]:
        for event in run.runner._invoke(action, run_id):
            run.orchestrator.handle(run_id, event)
        assert action.invocation_id not in run.orchestrator.state(run_id).invocations
        return 0, _fixture("codex-turn.jsonl")

    assert _runner(run, "codex", process)._invoke(action, run_id) == []
    state = run.orchestrator.state(run_id)
    assert state.usage_by_purpose[action.purpose.value].total >= 1020
    assert state.usage_by_invocation[action.invocation_id].total >= 1020


def test_all_antigravity_attempts_are_recorded_without_recounting_resumed_usage(
    org: Org, harness: Callable[..., Any]
) -> None:
    run = harness(org)
    run_id, _ = run.orchestrator.start("Goal")
    action = run.orchestrator.open_actions(run_id)[0]
    outputs = iter(
        [
            _fixture("antigravity-first.json") + '\nAGY_ERROR: {"short_error":"server shutting down"}',
            _fixture("antigravity-resumed.json"),
        ]
    )
    _runner(run, "antigravity", lambda *_: (0, next(outputs)))._invoke(action, run_id)
    state = run.orchestrator.state(run_id)
    assert state.usage.total == 27588 and state.usage.cached_input_tokens == 11566
    assert state.usage.unknown_model_calls == 2


def test_restarted_codex_sessions_keep_usage_from_the_attempt_without_tools(
    org: Org, harness: Callable[..., Any]
) -> None:
    run = harness(org)
    run_id, _ = run.orchestrator.start("Goal")
    action = run.orchestrator.open_actions(run_id)[0]
    outputs = iter([_fixture("codex-turn.jsonl") + "\nNO_ROLE_TOOLS", _fixture("codex-turn.jsonl")])
    _runner(run, "codex", lambda *_: (0, next(outputs)))._invoke(action, run_id)
    assert run.orchestrator.state(run_id).usage.total == 2040


def test_missing_usage_and_timeout_keep_known_counts_and_expose_uncertainty(
    org: Org, harness: Callable[..., Any]
) -> None:
    run = harness(org)
    run_id, _ = run.orchestrator.start("Goal")
    action = run.orchestrator.open_actions(run_id)[0]
    calls = 0

    def process(*_: Any) -> tuple[int, str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return 0, _fixture("codex-turn.jsonl") + "\nNO_ROLE_TOOLS"
        raise subprocess.TimeoutExpired("codex", 1, output=b"incomplete result")

    failures = _runner(run, "codex", process)._invoke(action, run_id)
    assert failures and "didn't finish" in failures[0].reason
    state = run.orchestrator.state(run_id)
    assert state.usage.total == 1020 and state.usage.unavailable_records == 1
    assert state.usage.model_usage_status == "partial"
    step = next(r["data"] for r in read_records(run.orchestrator.audit_path(run_id)) if r["kind"] == "agent_step")
    assert step["tokens_used"] is None and step["usage_status"] == "partial"
    assert state.usage_by_invocation[action.invocation_id].unavailable_records == 1


def test_a_failed_attempt_after_verified_usage_keeps_the_step_partial(org: Org, harness: Callable[..., Any]) -> None:
    run = harness(org)
    run_id, _ = run.orchestrator.start("Goal")
    action = run.orchestrator.open_actions(run_id)[0]
    attempts = iter([(0, _fixture("codex-turn.jsonl") + "\nNO_ROLE_TOOLS"), (1, _fixture("codex-default.jsonl"))])
    _runner(run, "codex", lambda *_: next(attempts))._invoke(action, run_id)
    state = run.orchestrator.state(run_id)
    assert state.usage.total == 1020 and state.usage.model_usage_status == "partial"
    step = next(r["data"] for r in read_records(run.orchestrator.audit_path(run_id)) if r["kind"] == "agent_step")
    assert step["tokens_used"] is None and step["usage_status"] == "partial"
    assert step["usage"]["unverified_records"] == 1


def test_a_step_removes_its_startup_folder_even_when_auditing_fails(
    org: Org, harness: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    run = harness(org)
    run_id, _ = run.orchestrator.start("Goal")
    action = run.orchestrator.open_actions(run_id)[0]
    folders: list[Path] = []
    record = run.orchestrator.record

    def fail_audit(run_id: str, kind: str, data: dict[str, Any]) -> None:
        if kind == "agent_step":
            raise OSError("audit unavailable")
        record(run_id, kind, data)

    def process(argv: list[str], stdin: str, env: dict[str, str], cwd: Path, timeout: int) -> tuple[int, str]:
        folders.append(cwd.parent)
        return 0, _fixture("codex-turn.jsonl")

    monkeypatch.setattr(run.orchestrator, "record", fail_audit)
    try:
        with pytest.raises(OSError, match="audit unavailable"):
            _runner(run, "codex", process)._invoke(action, run_id)
        assert folders and all(not folder.exists() for folder in folders)
    finally:
        for folder in folders:
            shutil.rmtree(folder, ignore_errors=True)


def test_finished_deliverables_refresh_when_exact_usage_arrives_late(org: Org, tmp_path: Path) -> None:
    from try_professional.adapters.api_runner.runner import ApiRunner, ModelRoutes
    from try_professional.adapters.api_runner.scripted import ScriptedClient

    orchestrator = Orchestrator(org, tmp_path / "home", deliverables_dir=tmp_path / "out")
    try:
        runner = ApiRunner(orchestrator, ScriptedClient(), ModelRoutes({"standard": "scripted"}))
        result = runner.run("Goal")
        assert result.status == "done"
        before = orchestrator.state(result.run_id).usage.total
        orchestrator.handle(
            result.run_id,
            UsageRecorded(
                invocation_id="closed-step",
                role=org.root_role,
                purpose="retrospective",
                model="codex",
                input_tokens=1000,
                cached_input_tokens=980,
                output_tokens=20,
                input_tokens_includes_cache=True,
                usage_source="codex: turn.completed usage",
                calls=None,
            ),
        )
        index = (tmp_path / "out" / result.run_id / "index.md").read_text(encoding="utf-8")
        assert f"Model tokens (exact): {before + 1020}" in index
        assert "Tool traffic:" in index and "separate from model usage" in index
        assert orchestrator.state(result.run_id).usage_by_purpose["retrospective"].total >= 1020
    finally:
        orchestrator.close()


@pytest.mark.parametrize("tool_budget", [True, False])
@pytest.mark.parametrize("cancelled", [True, False])
def test_public_stopped_runs_export_the_stopping_counter_and_refresh_separated_late_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tool_budget: bool,
    cancelled: bool,
) -> None:
    import yaml

    from try_professional.config import load_config
    from try_professional.org import load_org

    monkeypatch.chdir(tmp_path)
    assert main(["init", "--company", "Test", "--skip-model"]) == 0
    config = load_config(tmp_path)
    org_path = config.org_dir / "org.yaml"
    document = yaml.safe_load(org_path.read_text(encoding="utf-8"))
    limit = "mcp_max_tokens_per_run" if tool_budget else "max_tokens_per_run"
    document["pipeline"]["budget"][limit] = 1000
    org_path.write_text(yaml.safe_dump(document), encoding="utf-8")
    orchestrator = Orchestrator(load_org(config.org_dir), config.home, deliverables_dir=tmp_path / "out")
    try:
        service = McpService(orchestrator)
        run_id = service.run_start("Goal")["run_id"]
        action = orchestrator.open_actions(run_id)[0]
        workspace = orchestrator.workspace(run_id)
        (workspace / "traffic.txt").write_text("evidence " * 2000, encoding="utf-8")
        assert service.workspace_read(run_id, action.invocation_id, "traffic.txt").startswith("evidence")

        def model_usage() -> None:
            # Controlled reporting data uses the supported CLI contract; it is
            # not a live provider measurement or independent billing evidence.
            usage = cli_usage("codex", _fixture("codex-turn.jsonl"))
            orchestrator.handle(
                run_id,
                UsageRecorded(
                    invocation_id=action.invocation_id,
                    role=action.role,
                    purpose=action.purpose.value,
                    model="codex",
                    input_tokens=usage.input_tokens,
                    cached_input_tokens=usage.cached_input_tokens,
                    output_tokens=usage.output_tokens,
                    input_tokens_includes_cache=True,
                    calls=None,
                    usage_source=usage.source,
                ),
            )

        if not tool_budget:
            model_usage()
        assert service.run_next_action(run_id)["status"] == "blocked"
        status = service.run_status(run_id)
        tools = status["estimated_tool_traffic"]
        draft = tmp_path / "out" / run_id / "draft" / "index.md"
        index = draft.read_text(encoding="utf-8")
        count = tools["total_tokens"] if tool_budget else 1020
        label = "estimated tool tokens" if tool_budget else "model tokens"
        assert f"at {count:,} {label}" in index.splitlines()[0]
        assert "at 0 tokens" not in index.splitlines()[0]
        assert f"Model tokens ({'unavailable' if tool_budget else 'exact'}): {0 if tool_budget else 1020}" in index
        assert f"Tool traffic: about {tools['total_tokens']} tokens" in index
        if cancelled:
            orchestrator.cancel(run_id)
        model_usage()
        index = draft.read_text(encoding="utf-8")
        total = 1020 if tool_budget else 2040
        assert f"Model tokens (exact): {total} ({1000 if tool_budget else 2000} input, " in index
        assert f"{980 if tool_budget else 1960} of input cached, {20 if tool_budget else 40} output)" in index
        assert (
            f"Tool traffic: about {tools['total_tokens']} tokens ({tools['input_tokens']} input, {tools['output_tokens']} output)"
            in index
        )
        assert "separate from model usage" in index
        assert service.run_status(run_id)["model_usage"]["total_tokens"] == total
        assert orchestrator.state(run_id).phase.value == ("failed" if cancelled else "blocked")
    finally:
        orchestrator.close()
    capsys.readouterr()
    assert main(["status", run_id]) == 0
    output = capsys.readouterr().out
    assert f"Model tokens (exact): {total}" in output
    assert f"Tool traffic: about {tools['total_tokens']} tokens" in output


def test_api_usage_and_tool_traffic_remain_separate_in_cli_result_and_monthly_status(
    org: Org, harness: Callable[..., Any], capsys: pytest.CaptureFixture[str]
) -> None:
    run = harness(org)
    run_id, _ = run.orchestrator.start("Goal")
    action = run.orchestrator.open_actions(run_id)[0]
    run.orchestrator.handle(
        run_id,
        UsageRecorded(
            invocation_id=action.invocation_id,
            role=action.role,
            model="api-model",
            input_tokens=1000,
            cached_input_tokens=980,
            output_tokens=20,
            input_tokens_includes_cache=True,
        ),
    )
    run.orchestrator.handle(
        run_id,
        UsageRecorded(
            invocation_id=action.invocation_id,
            role=action.role,
            model=ESTIMATED_BY_SERVER,
            input_tokens=20_000_000,
            output_tokens=16_000_000,
        ),
    )
    result = _runner(run, "codex", lambda *_: (0, ""))._result(run_id, "blocked")
    _print_result(result)
    _status_tokens(run.orchestrator)
    output = capsys.readouterr().out
    assert "Model tokens (exact): 1020" in output
    assert "about 36000000" in output and "estimated tool tokens" in output
    assert "separate from model usage" in output
    assert result.calls == 1 and result.input_tokens == 1000
    assert run.orchestrator.company.tokens_by_month()[0]["estimated_tool_tokens"] == 36_000_000
    assert run.orchestrator.state(run_id).phase.value != "blocked"


def test_historical_combined_snapshot_is_preserved_without_claiming_a_model_split() -> None:
    usage = Usage.model_validate(
        {"input_tokens": 20_000_000, "output_tokens": 16_000_000, "calls": 500, "estimated": True}
    )
    assert usage.total == 0 and usage.unverified_legacy_tokens == 36_000_000
    assert usage.model_usage_status == "unavailable" and usage.estimated_tool_tokens == 0
    assert "Historical combined usage" in "\n".join(usage.lines())
    assert Usage.model_validate_json(usage.model_dump_json()).unverified_legacy_tokens == 36_000_000


def test_historical_model_snapshot_without_a_cache_convention_remains_unverified() -> None:
    usage = Usage.model_validate({"input_tokens": 1000, "output_tokens": 20, "calls": 1})
    assert usage.total == 1020 and usage.model_usage_status == "partial"
    assert usage.unverified_records == 1


def test_contradictory_antigravity_totals_are_unverified() -> None:
    result = json.loads(_fixture("antigravity-resumed.json"))
    result["usage"]["total_tokens"] = 99
    assert cli_usage("antigravity", json.dumps(result)).status == "unverified"


def test_company_migration_preserves_historical_combined_totals_as_unverified(tmp_path: Path) -> None:
    path = tmp_path / "company.sqlite"
    connection = sqlite3.connect(path)
    for number, script in enumerate(_MIGRATIONS[:4], start=1):
        connection.executescript(script + f"PRAGMA user_version={number};")
    connection.execute(
        "INSERT INTO commands VALUES ('run-old','Goal','done','small','2026-11-15','2026-11-15',"
        "20000000,0,16000000,500)"
    )
    connection.commit()
    connection.close()
    store = CompanyStore(path)
    try:
        row = store.tokens_by_command()[0]
        assert row["input"] == 0 and row["output"] == 0
        assert row["unverified_legacy_tokens"] == 36_000_000
        assert row["model_usage_status"] == "unavailable"
    finally:
        store.close()
