"""A resumed run must neither repeat outside work nor leave the next answer unowned."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from try_professional.adapters.api_runner.scripted import default_payload
from try_professional.adapters.mcp import McpService, ToolError
from try_professional.cli import main
from try_professional.core.orchestrator import Orchestrator
from try_professional.core.outside import OutsideStore
from try_professional.org import Org

NOW = "2026-11-16T00:00:00+00:00"


@pytest.fixture
def company(org: Org, tmp_path: Path) -> Iterator[Orchestrator]:
    orchestrator = Orchestrator(org, tmp_path / "home")
    yield orchestrator
    orchestrator.close()


def _until(service: McpService, purpose: str) -> tuple[str, dict[str, Any]]:
    current = service.run_start("Build a health check")
    run_id = current["run_id"]
    for _ in range(10):
        action = current["actions"][0]
        if action["purpose"] == purpose:
            return run_id, action
        state = service.orchestrator.state(run_id)
        payload = default_payload(state.invocations[action["invocation_id"]], state, service.org)
        current = service.artifact_submit(run_id, action["invocation_id"], payload)
        assert current["accepted"], current
    raise AssertionError(f"No {purpose} action")


def test_reproposing_completed_work_after_reopening_reuses_its_content_key(tmp_path: Path) -> None:
    path = tmp_path / "outside.sqlite"
    store = OutsideStore(path)
    first = store.hold(run_id="r1", role="x/a", command="publish draft", workspace=tmp_path, at=NOW)
    assert store.decide_held(first, "approved", by="a person", at=NOW)
    item = store.held_item(first)
    assert item is not None and len(item.idempotency_key) == 64
    key = item.idempotency_key
    store.close()
    store = OutsideStore(path)
    try:
        again = store.hold(run_id="r2", role="x/b", command="publish draft", workspace=tmp_path, at=NOW)
        assert again == first
        assert store.held_item(again).idempotency_key == key
        assert not store.decide_held(again, "approved", by="a person", at=NOW)
        assert store.hold(run_id="r2", role="x/b", command="publish another draft", workspace=tmp_path, at=NOW) != first
        assert (
            store.hold(run_id="r2", role="x/b", command="publish draft", workspace=tmp_path / "other", at=NOW) != first
        )
    finally:
        store.close()


def test_retried_cli_approval_reports_already_done_without_executing_again(
    company: Orchestrator, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    number = company.outside.hold(run_id="r1", role="x/a", command="publish draft", workspace=tmp_path, at=NOW)
    calls = []

    def execute(workspace: Path, command: str) -> str:
        calls.append((workspace, command))
        return "exit code 0\npublished"

    home, org = company.home, company.org
    monkeypatch.setattr("try_professional.cli._here", lambda _: tmp_path)
    monkeypatch.setattr("try_professional.cli._outside_orchestrator", lambda *a, **kw: Orchestrator(org, home))
    monkeypatch.setattr("try_professional.cli._person_at_terminal", lambda: True)
    monkeypatch.setattr("try_professional.adapters.api_runner.tools.execute", execute)
    assert main(["held", "approve", str(number)]) == 0
    capsys.readouterr()
    assert main(["held", "approve", str(number)]) == 0
    assert "already done" in capsys.readouterr().out.lower()
    assert calls == [(tmp_path, "publish draft")]


def test_held_lists_the_content_key(
    company: Orchestrator, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    number = company.outside.hold(run_id="r1", role="x/a", command="publish draft", workspace=tmp_path, at=NOW)
    home, org = company.home, company.org
    monkeypatch.setattr("try_professional.cli._here", lambda _: tmp_path)
    monkeypatch.setattr("try_professional.cli._outside_orchestrator", lambda *a, **kw: Orchestrator(org, home))
    assert main(["held"]) == 0
    assert f"idempotency key: {company.outside.held_item(number).idempotency_key}" in capsys.readouterr().out


def test_an_old_store_preserves_ids_and_prevents_duplicate_approvals(tmp_path: Path) -> None:
    path = tmp_path / "outside.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE held (
                id INTEGER PRIMARY KEY, run_id TEXT, role TEXT, command TEXT, workspace TEXT,
                created_at TEXT, status TEXT, decided_by TEXT, decided_at TEXT, output TEXT DEFAULT '',
                checks TEXT DEFAULT ''
            );
        """)
        for number in (1, 2):
            connection.execute(
                "INSERT INTO held (id, run_id, role, command, workspace, created_at, status) "
                "VALUES (?, 'old', 'x/a', 'publish draft', ?, ?, 'waiting')",
                (number, str(tmp_path), NOW),
            )
        connection.execute(
            "INSERT INTO held (id, run_id, role, command, workspace, created_at, status) "
            "VALUES (3, 'old', 'x/a', 'publish another draft', ?, ?, 'approved')",
            (str(tmp_path), NOW),
        )
    first, second = OutsideStore(path), OutsideStore(path)
    try:
        assert [h.id for h in first.held()] == [1, 2]
        assert first.held_item(1).idempotency_key == second.held_item(2).idempotency_key
        assert first.held_execution(3).status == "running", "a legacy approval without output is unverified"
        with ThreadPoolExecutor(max_workers=2) as workers:
            claims = list(
                workers.map(
                    lambda pair: pair[0].decide_held(pair[1], "running", by="a person", at=NOW),
                    [(first, 1), (second, 2)],
                )
            )
        assert sorted(claims) == [False, True]
        winner = 1 if claims[0] else 2
        first.finish_held(winner, "exit code 0\npublished")
        assert second.held_execution(3 - winner).output == "exit code 0\npublished"
        assert second.hold(run_id="new", role="x/b", command="publish draft", workspace=tmp_path, at=NOW) == winner
    finally:
        first.close()
        second.close()


def test_an_interrupted_execution_is_never_repeated_or_claimed_completed(company: Orchestrator, tmp_path: Path) -> None:
    first = company.outside.hold(run_id="r1", role="x/a", command="publish draft", workspace=tmp_path, at=NOW)
    calls = []

    def interrupted(workspace: Path, command: str) -> str:
        calls.append(command)
        raise RuntimeError("lost connection after sending")

    with pytest.raises(RuntimeError, match="lost connection"):
        company.run_held(first, interrupted, by="a person")
    path = company.outside.path
    company.outside.close()
    company.outside = OutsideStore(path)
    again = company.outside.hold(run_id="r2", role="x/b", command="publish draft", workspace=tmp_path, at=NOW)
    assert again == first
    assert company.run_held(again, interrupted, by="a person") is None
    assert calls == ["publish draft"]
    assert company.outside.held_item(first).status == "running"


def test_an_escalation_names_one_reply_owner_and_accepts_only_that_roles_answer(company: Orchestrator) -> None:
    operator = McpService(company)
    run_id, work = _until(operator, "execute")
    worker = McpService(company, bound_role=work["role"])
    result = worker.escalate(run_id, work["invocation_id"], "Need a decision")
    owner = company.org.role(work["role"]).escalation
    assert result["next_reply_role"] == owner
    assert worker.run_next_action(run_id)["next_reply_role"] == owner
    assert result["actions"] == [], "the waiting role sees ownership without being assigned the manager's work"
    manager = McpService(company, bound_role=owner)
    reply = manager.run_next_action(run_id)["actions"][0]
    assert reply["next_reply_role"] == owner
    assert f"Next reply: {owner}." in reply["assignment"]
    with pytest.raises(ToolError, match=f"assigned to {owner}"):
        worker.artifact_submit(run_id, reply["invocation_id"], {"decision": "resume", "guidance": "Proceed"})
    assert reply["invocation_id"] in company.state(run_id).invocations
    resumed = manager.artifact_submit(run_id, reply["invocation_id"], {"decision": "resume", "guidance": "Proceed"})
    assert resumed["accepted"] and resumed["next_reply_role"] == work["role"]
    repeated = worker.escalate(run_id, work["invocation_id"], "Still need a decision")
    assert repeated["next_reply_role"] == company.org.role(owner).escalation


def test_an_escalation_at_the_top_names_the_person_as_the_next_reply_owner(company: Orchestrator) -> None:
    service = McpService(company, bound_role=company.org.root_role)
    first = service.run_start("Need a decision")
    result = service.escalate(first["run_id"], first["actions"][0]["invocation_id"], "Only a person can decide")
    assert result["next_reply_role"] == "human"
    assert result["needs_a_person"][0]["next_reply_role"] == "human"


def test_a_handoff_names_its_receiving_head_as_the_next_reply_owner(company: Orchestrator) -> None:
    run_id, dispatch = _until(McpService(company), "dispatch")
    head = McpService(company, bound_role=dispatch["role"])
    result = head.handoff_request(
        run_id,
        dispatch["invocation_id"],
        {
            "from_department": "engineering",
            "to_department": "research",
            "request": "Find supporting evidence",
            "acceptance": ["Sources are supplied"],
            "because": "Research can gather the evidence",
        },
    )
    owner = company.org.departments["research"].head_ref
    assert result["accepted"] and result["next_reply_role"] == owner
    reply = McpService(company, bound_role=owner).run_next_action(run_id)["actions"][0]
    assert reply["next_reply_role"] == owner
    assert f"Next reply: {owner}." in reply["assignment"]


def test_a_report_names_its_reviewer_as_the_next_reply_owner(company: Orchestrator) -> None:
    run_id, work = _until(McpService(company), "execute")
    state = company.state(run_id)
    payload = default_payload(state.invocations[work["invocation_id"]], state, company.org)
    result = McpService(company, bound_role=work["role"]).artifact_submit(run_id, work["invocation_id"], payload)
    owner = state.tasks[work["task_id"]].reviewer_role
    assert result["accepted"] and result["next_reply_role"] == owner
    reply = McpService(company, bound_role=owner).run_next_action(run_id)["actions"][0]
    assert reply["next_reply_role"] == owner
    assert f"Next reply: {owner}." in reply["assignment"]
