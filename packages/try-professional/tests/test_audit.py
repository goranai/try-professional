"""Full auditability, and resuming a run after a crash."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from try_professional.adapters.api_runner.clients import ChatRequest, ChatResponse
from try_professional.adapters.api_runner.runner import ApiRunner, ModelRoutes
from try_professional.adapters.api_runner.scripted import ScriptedClient
from try_professional.core.artifacts import make_artifact
from try_professional.core.audit import AuditLog, verify_log
from try_professional.core.orchestrator import Orchestrator
from try_professional.core.pipeline import IllegalEvent, Submission
from try_professional.org import Org


def _write_log(path: Path, count: int = 5) -> AuditLog:
    log = AuditLog(path)
    for n in range(count):
        log.append(f"t{n}", "event", {"n": n, "text": "halo"})
    return log


class TestChain:
    def test_clean_log_verifies(self, tmp_path: Path) -> None:
        log = _write_log(tmp_path / "a.jsonl")
        result = verify_log(log.path, log.head)
        assert result.ok and result.records == 5

    def test_edited_record_is_detected(self, tmp_path: Path) -> None:
        log = _write_log(tmp_path / "a.jsonl")
        lines = log.path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[2])
        record["data"]["text"] = "edited"
        lines[2] = json.dumps(record, sort_keys=True)
        log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = verify_log(log.path)
        assert not result.ok and "line 3" in (result.problem or "")

    def test_removed_middle_record_is_detected(self, tmp_path: Path) -> None:
        log = _write_log(tmp_path / "a.jsonl")
        lines = log.path.read_text(encoding="utf-8").splitlines()
        del lines[2]
        log.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert not verify_log(log.path).ok

    def test_removed_tail_is_detected_using_the_recorded_head(self, tmp_path: Path) -> None:
        log = _write_log(tmp_path / "a.jsonl")
        head = log.head
        lines = log.path.read_text(encoding="utf-8").splitlines()
        log.path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        assert verify_log(log.path).ok, "without the head, a shortened chain still looks valid"
        result = verify_log(log.path, head)
        assert not result.ok and "removed" in (result.problem or "")

    def test_reopening_continues_the_chain(self, tmp_path: Path) -> None:
        _write_log(tmp_path / "a.jsonl")
        log = AuditLog(tmp_path / "a.jsonl")
        log.append("t9", "event", {"n": 9})
        assert verify_log(log.path, log.head).records == 6

    def test_opening_a_corrupt_log_fails(self, tmp_path: Path) -> None:
        log = _write_log(tmp_path / "a.jsonl")
        log.path.write_text("not json\n", encoding="utf-8")
        with pytest.raises(ValueError, match="corrupt"):
            AuditLog(log.path)


class TestRunAudit:
    def test_every_decision_is_logged_and_verifies(self, org: Org, harness: Callable[..., Any]) -> None:
        run = harness(org)
        result = run.runner.run("Goal")
        assert run.orchestrator.verify_audit(result.run_id).ok

        records = run.records(result.run_id)
        kinds = {r["kind"] for r in records}
        assert {"run_started", "event", "action", "phase", "memory_recall", "skill_search"} <= kinds

        state = run.orchestrator.state(result.run_id)
        submitted = [r for r in records if r["kind"] == "event" and r["data"].get("kind") == "artifact_submitted"]
        produced = [a for a in state.artifacts.values() if a.type not in {"TaskBrief", "SkillResolution"}]
        assert len(submitted) == len(produced)
        activated = [r["data"]["department"] for r in records if r["data"].get("kind") == "department_activated"]
        assert set(activated) == set(state.activated)

    def test_phases_follow_the_lifecycle(self, org: Org, harness: Callable[..., Any]) -> None:
        run = harness(org)
        result = run.runner.run("Goal")
        seen = [r["data"]["to"] for r in run.records(result.run_id) if r["kind"] == "phase"]
        ordered = [
            "planning",
            "dispatch",
            "skill_resolution",
            "execution",
            "review",
            "integration",
            "retrospective",
            "done",
        ]
        positions = [seen.index(p) for p in ordered]
        assert positions == sorted(positions), seen

    def test_a_rejected_event_is_logged_as_a_violation(self, org: Org, harness: Callable[..., Any]) -> None:
        run = harness(org)
        run_id, actions = run.orchestrator.start("Goal")
        with pytest.raises(IllegalEvent):
            run.orchestrator.handle(
                run_id, Submission(invocation_id=actions[0].invocation_id, actor="sales/sales-lead", payload={})
            )
        violations = [r for r in run.records(run_id) if r["kind"] == "violation"]
        assert violations[0]["data"]["code"] == "WRONG_ACTOR"
        assert run.orchestrator.state(run_id).counters.violations == 1
        assert run.orchestrator.verify_audit(run_id).ok

    def test_no_keys_reach_the_log(
        self, org: Org, harness: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-do-not-log")
        run = harness(org)
        result = run.runner.run("Goal")
        assert "sk-secret" not in run.orchestrator.audit_path(result.run_id).read_text(encoding="utf-8")


class _Crash(BaseException):
    pass


class _CrashingClient(ScriptedClient):
    def __init__(self, crash_after: int) -> None:
        super().__init__()
        self.crash_after = crash_after

    def complete(self, request: ChatRequest) -> ChatResponse:
        if len(self.calls) >= self.crash_after:
            raise _Crash
        return super().complete(request)


ROUTES = ModelRoutes({"standard": "m", "executive": "m", "fast": "m"})


@pytest.mark.parametrize("crash_after", [1, 3, 5, 6])
def test_run_resumes_after_a_crash(org: Org, tmp_path: Path, crash_after: int) -> None:
    first = Orchestrator(org, tmp_path)
    crashing = _CrashingClient(crash_after)
    runner = ApiRunner(first, crashing, ROUTES)
    with pytest.raises(_Crash):
        runner.run("Goal", run_id="r1")
    calls_before = len(crashing.calls)
    first.close()

    second = Orchestrator(org, tmp_path)
    client = ScriptedClient()
    result = ApiRunner(second, client, ROUTES).resume("r1")
    assert result.status == "done"
    assert second.verify_audit("r1").ok
    assert calls_before == crash_after
    assert len(client.calls) > 0
    state = second.state("r1")
    assert state.final_artifact_id and state.retrospective_artifact_id
    second.close()


def test_artifact_id_is_stable_and_content_addressed() -> None:
    charter = {"original_goal": "g", "restated_goal": "g", "user_language": "id", "success_criteria": ["c"]}
    make = lambda created: make_artifact(  # noqa: E731
        "GoalCharter", charter, run_id="r", produced_by="executive/ceo", created_at=created
    )
    assert make("t1").artifact_id == make("t2").artifact_id
    other = make_artifact("GoalCharter", {**charter, "user_language": "es"}, run_id="r", produced_by="executive/ceo")
    assert other.artifact_id != make("t1").artifact_id
