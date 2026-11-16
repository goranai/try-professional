"""The roster can shrink only when a person decides from recorded cost and contribution."""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from try_professional import Company, CompanyError
from try_professional.adapters.api_runner.prompts import user_prompt
from try_professional.adapters.api_runner.scripted import ScriptedClient, default_payload
from try_professional.cli import main
from try_professional.core.artifacts import OrgChange, parse_payload
from try_professional.core.audit import AuditLog, archive_log, read_records
from try_professional.core.company import CompanyStore
from try_professional.core.pipeline import Purpose, Submission
from try_professional.core.pipeline.planning import org_change_problem
from try_professional.core.pipeline.state import Invocation
from try_professional.core.scorecard import protected_roles, scorecard
from try_professional.org import Org

NOW = "2026-11-15T00:00:00+00:00"
WORKER = "engineering/backend"
REVIEWER = "qa/qa-lead"


def _hand_in(log: AuditLog, role: str, purpose: str, payload: dict[str, Any], *, refused: bool = False) -> None:
    log.append(
        NOW,
        "action",
        {"kind": "invoke_role", "invocation_id": "inv-1", "role": role, "purpose": purpose, "task_id": "wp1/t1"},
    )
    log.append(
        NOW, "event", {"kind": "artifact_submitted", "invocation_id": "inv-1", "actor": role, "payload": payload}
    )
    if refused:
        log.append(NOW, "rejected", {"invocation_id": "inv-1"})


def test_cost_and_contribution_can_be_traced_across_plain_and_archived_runs(tmp_path: Path, org: Org) -> None:
    log = AuditLog(tmp_path / "runs" / "run-a" / "audit.jsonl")
    _hand_in(log, WORKER, "execute", {"summary": "first result"})
    _hand_in(log, REVIEWER, "review", {"decision": "changes_requested", "issues": ["Fix the result"]})
    _hand_in(log, WORKER, "execute", {"summary": "corrected result"})
    _hand_in(log, REVIEWER, "review", {"decision": "approve", "subject_artifact_id": "a"})
    log.append(NOW, "event", {"kind": "usage_recorded", "role": WORKER, "input_tokens": 60, "output_tokens": 20})
    log.append(NOW, "outcome_tracked", {"id": 1, "role": WORKER, "ref": "change 1"})
    log.append(NOW, "outcome_settled", {"id": 1, "status": "merged"})
    log.append(NOW, "outcome_settled", {"id": 1, "status": "posted"})
    archive_log(log.path)
    other = AuditLog(tmp_path / "runs" / "run-b" / "audit.jsonl")
    _hand_in(other, WORKER, "execute", {"summary": "corrected result"})
    _hand_in(other, REVIEWER, "review", {"decision": "approve", "subject_artifact_id": "b"})
    other.append(
        NOW,
        "event",
        {
            "kind": "usage_recorded",
            "role": REVIEWER,
            "input_tokens": 15,
            "output_tokens": 5,
            "model": "estimated by server",
        },
    )
    other.append(NOW, "usage_self_reported", {"role": REVIEWER, "tokens": 999})
    card = scorecard(tmp_path, org)
    worker, reviewer = card.roles[WORKER], card.roles[REVIEWER]
    assert card.runs == 2
    assert worker.tokens == 80 and worker.token_share == 1.0
    assert reviewer.tokens == 0 and reviewer.estimated_tokens == 20
    assert reviewer.token_share == 0.0
    assert (worker.accepted_work, worker.repeated, worker.outcomes, worker.changed, worker.activities) == (
        2,
        1,
        1,
        3,
        4,
    )
    assert (reviewer.reviews_changed, reviewer.approval_only, reviewer.repeated, reviewer.activities) == (1, 2, 1, 3)
    assert "3/4" in worker.line() and "1/3" in reviewer.line()
    assert any("run-a/audit.jsonl#4" in e and "followed by" in e for e in reviewer.evidence)
    assert card.roles[org.root_role].activities == 0
    assert "unobserved" in card.roles[org.root_role].line()


def test_refused_work_and_unmade_review_requests_do_not_get_contribution_credit(tmp_path: Path, org: Org) -> None:
    log = AuditLog(tmp_path / "runs" / "run-a" / "audit.jsonl")
    _hand_in(log, WORKER, "execute", {"summary": "result"})
    _hand_in(log, REVIEWER, "review", {"decision": "changes_requested", "issues": ["Fix it"]})
    _hand_in(log, WORKER, "execute", {"summary": "refused"}, refused=True)
    _hand_in(log, WORKER, "execute", {"summary": "result"})
    card = scorecard(tmp_path, org)
    assert card.roles[WORKER].accepted_work == 1
    assert card.roles[WORKER].submissions == 3
    assert card.roles[REVIEWER].reviews_changed == 0
    assert any("refused" in e for e in card.roles[WORKER].evidence)


def test_corrupt_audit_is_reported_as_unverified_instead_of_used(tmp_path: Path, org: Org) -> None:
    log = AuditLog(tmp_path / "runs" / "run-a" / "audit.jsonl")
    _hand_in(log, WORKER, "execute", {"summary": "result"})
    log.path.write_text(log.path.read_text(encoding="utf-8").replace("result", "edited"), encoding="utf-8")
    card = scorecard(tmp_path, org)
    assert card.runs == 0 and card.roles[WORKER].accepted_work == 0
    assert "Unverified" in card.problems[0]


@pytest.mark.parametrize("input_includes_cache,inputs", [(False, 10), (True, 990)])
def test_token_shares_include_cache_reads_and_writes_once(
    tmp_path: Path, org: Org, input_includes_cache: bool, inputs: int
) -> None:
    log = AuditLog(tmp_path / "runs" / "run-a" / "audit.jsonl")
    for role, usage in (
        (WORKER, {"input_tokens": inputs, "cached_input_tokens": 970, "cache_creation_input_tokens": 10}),
        (REVIEWER, {"input_tokens": 90}),
    ):
        log.append(
            NOW,
            "event",
            {
                "kind": "usage_recorded",
                "role": role,
                "output_tokens": 10,
                "input_tokens_includes_cache": input_includes_cache,
                **usage,
            },
        )
    card = scorecard(tmp_path, org)
    assert card.roles[WORKER].tokens == 1000
    assert card.roles[WORKER].token_share == pytest.approx(1000 / 1100)
    assert card.roles[REVIEWER].tokens == 100
    assert card.roles[REVIEWER].token_share == pytest.approx(100 / 1100)
    assert card.problems == []


def test_historical_usage_with_unknown_cache_convention_marks_all_shares_provisional(tmp_path: Path, org: Org) -> None:
    log = AuditLog(tmp_path / "runs" / "run-a" / "audit.jsonl")
    log.append(
        NOW,
        "event",
        {
            "kind": "usage_recorded",
            "role": WORKER,
            "model": "claude-sonnet-4-5",
            "input_tokens": 10,
            "output_tokens": 10,
            "cached_input_tokens": 980,
        },
    )
    card = scorecard(tmp_path, org)
    assert card.roles[WORKER].tokens == 20
    assert card.roles[WORKER].unverified_usage_records == 1
    assert "20*" in card.roles[WORKER].line()
    assert "cache convention unverified" in card.problems[0]
    assert "Token shares are provisional" in card.problems[0]
    assert "all shares are provisional" in "\n".join(card.lines())


# QA saw different xdist collections across a second boundary; fixed gzip timestamps keep the IDs identical.
@pytest.mark.parametrize(
    "broken",
    [
        b"not gzip",
        gzip.compress(b"\xff", mtime=0),
        gzip.compress(b"audit", mtime=0)[:-4],
        gzip.compress(b"[]\n", mtime=0),
    ],
)
def test_unreadable_archives_are_excluded_while_verified_runs_keep_the_cli_scorecard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], broken: bytes
) -> None:
    company = Company.create(tmp_path / "company", name="Test")
    home = company.root / ".trypro"
    log = AuditLog(home / "runs" / "run-a" / "audit.jsonl")
    _hand_in(log, WORKER, "execute", {"summary": "result"})
    archive_log(log.path)
    path = home / "runs" / "run-b" / "audit.jsonl.gz"
    path.parent.mkdir(parents=True)
    path.write_bytes(broken)
    card = scorecard(home, company.org)
    assert card.runs == 1 and card.roles[WORKER].accepted_work == 1
    assert "run-b excluded" in card.problems[0]
    monkeypatch.chdir(company.root)
    assert main(["org", "scorecard"]) == 0
    output = capsys.readouterr().out
    assert "Role scorecard from 1 audit log(s)" in output
    assert "run-b excluded" in output and WORKER in output


def _change(kind: str = "retire_role", ref: str = WORKER, into: str = "") -> dict[str, Any]:
    department, role_id = ref.split("/")
    return {
        "kind": kind,
        "department": department,
        "role_id": role_id,
        "into": into,
        "because": "Repeated cost deserves a person's review",
        "scorecard_evidence": ["audit reference"],
    }


def _propose(company: Company, change: dict[str, Any]) -> int:
    store = CompanyStore(company.root / ".trypro" / "company.sqlite")
    try:
        store.propose_org_change(change, source_run="run-a", proposed_by=company.org.root_role, at=NOW)
        return store.org_proposals()[0].id
    finally:
        store.close()


@pytest.mark.parametrize("kind", ["retire_role", "merge_role"])
def test_root_and_every_gate_reject_removal_even_at_the_person_decision(tmp_path: Path, kind: str) -> None:
    company = Company.create(tmp_path / "company", name="Test")
    before = (company.root / "org" / "org.yaml").read_text(encoding="utf-8")
    for ref in protected_roles(company.org):
        department = company.org.department_of(ref)
        target = next((f"{department.id}/{r}" for r in department.roles if f"{department.id}/{r}" != ref), ref)
        change = _change(kind, ref, target)
        assert "Cannot remove" in (org_change_problem([OrgChange.model_validate(change)], company.org) or "")
        number = _propose(company, change)
        with pytest.raises(CompanyError, match="Cannot remove"):
            company.decide_org_change(number, True, by="owner")
        assert company.org.has_role(ref)
        company.decide_org_change(number, False, by="owner")
    assert (company.root / "org" / "org.yaml").read_text(encoding="utf-8") == before


def test_retirement_waits_for_a_person_and_rejection_keeps_the_role(tmp_path: Path) -> None:
    company = Company.create(tmp_path / "company", name="Test")
    number = _propose(company, _change())
    assert company.org.has_role(WORKER)
    assert company.decide_org_change(number, False, by="owner") is None
    assert company.org.has_role(WORKER)
    assert company.org_proposals() == []


def test_person_accepted_retirement_removes_the_role_and_refreshes_agent_files(tmp_path: Path) -> None:
    company = Company.create(tmp_path / "company", name="Test")
    company.setup_claude_code()
    number = _propose(company, _change())
    affected = company.decide_org_change(number, True, by="owner")
    assert affected is not None and affected.ref == WORKER
    assert not company.org.has_role(WORKER)
    assert not (company.root / ".claude" / "agents" / "trypro-engineering-backend.md").exists()
    assert company.validate() == []


@pytest.mark.parametrize("ref,into", [(WORKER, "engineering/frontend"), ("engineering/tech-lead", WORKER)])
def test_person_accepted_merger_transfers_work_and_preserves_reporting_lines(
    tmp_path: Path, ref: str, into: str
) -> None:
    company = Company.create(tmp_path / "company", name="Test")
    source = company.org.role(ref)
    target = company.org.role(into)
    number = _propose(company, _change("merge_role", ref, into))
    company.decide_org_change(number, True, by="owner")
    org = company.org
    assert not org.has_role(ref)
    assert set(source.responsibilities) <= set(org.role(into).responsibilities)
    assert set(source.skill_needs) <= set(org.role(into).skill_needs)
    assert org.role(into).allowed_tools == target.allowed_tools
    assert company.validate() == []
    if source.id == "tech-lead":
        assert org.department_of(into).head_ref == into
        assert org.role(into).escalation == org.root_role
        assert org.role("engineering/frontend").escalation == into


def test_an_invalid_reduction_restores_files_and_keeps_the_proposal_waiting(tmp_path: Path) -> None:
    company = Company.create(tmp_path / "company", name="Test")
    path = company.root / "org" / "roles" / "engineering" / "tech-lead.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["reviewer"] = WORKER
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    worker_path = path.with_name("backend.yaml")
    document = yaml.safe_load(worker_path.read_text(encoding="utf-8"))
    document["reviewer"] = "engineering/tech-lead"
    worker_path.write_text(yaml.safe_dump(document), encoding="utf-8")
    before = {p: p.read_bytes() for p in (company.root / "org").rglob("*.yaml")}
    number = _propose(company, _change())
    with pytest.raises(CompanyError, match="wasn't removed"):
        company.decide_org_change(number, True, by="owner")
    assert {p: p.read_bytes() for p in (company.root / "org").rglob("*.yaml")} == before
    assert company.org_proposals()[0].id == number


def test_a_role_with_open_work_cannot_be_retired(tmp_path: Path) -> None:
    company = Company.create(tmp_path / "company", name="Test")
    orchestrator = company._orchestrator()
    try:
        run_id, actions = orchestrator.start("Build a service")
        for _ in range(20):
            action = next(a for a in actions if a.kind == "invoke_role")
            if action.role == WORKER and action.purpose == Purpose.EXECUTE:
                break
            state = orchestrator.state(run_id)
            payload = default_payload(state.invocations[action.invocation_id], state, company.org)
            actions = orchestrator.handle(
                run_id, Submission(invocation_id=action.invocation_id, actor=action.role, payload=payload)
            )
        else:
            pytest.fail("The worker was never assigned")
        number = _propose(company, _change())
        with pytest.raises(CompanyError, match="open work"):
            company.decide_org_change(number, True, by="owner")
        assert company.org.has_role(WORKER) and company.org_proposals()[0].id == number
    finally:
        orchestrator.close()


def test_the_last_refusal_before_escalation_is_never_counted_as_accepted_work(tmp_path: Path) -> None:
    company = Company.create(tmp_path / "company", name="Test")
    orchestrator = company._orchestrator()
    try:
        run_id, actions = orchestrator.start("Build a service")
        for _ in range(20):
            action = next(a for a in actions if a.kind == "invoke_role")
            if action.role == WORKER and action.purpose == Purpose.EXECUTE:
                break
            state = orchestrator.state(run_id)
            payload = default_payload(state.invocations[action.invocation_id], state, company.org)
            actions = orchestrator.handle(
                run_id, Submission(invocation_id=action.invocation_id, actor=action.role, payload=payload)
            )
        else:
            pytest.fail("The worker was never assigned")
        for _ in range(5):
            orchestrator.handle(run_id, Submission(invocation_id=action.invocation_id, actor=WORKER, payload={}))
            if action.invocation_id not in orchestrator.state(run_id).invocations:
                break
        assert action.invocation_id not in orchestrator.state(run_id).invocations
        row = orchestrator.role_scorecard().roles[WORKER]
        assert row.submissions > 1 and row.accepted_work == 0
    finally:
        orchestrator.close()


def test_identical_rework_is_accepted_repetition_and_does_not_credit_a_review(tmp_path: Path) -> None:
    company = Company.create(tmp_path / "company", name="Test")
    requested = False

    def override(invocation: Any, state: Any, org: Org) -> dict[str, Any] | None:
        nonlocal requested
        if invocation.purpose == Purpose.REVIEW and not requested:
            requested = True
            return {
                **default_payload(invocation, state, org),
                "decision": "changes_requested",
                "issues": [{"severity": "major", "description": "Correct the result"}],
            }
        return None

    result = company.run("Build a service", client=ScriptedClient(override))
    assert result.status == "done"
    orchestrator = company._orchestrator()
    try:
        card = orchestrator.role_scorecard()
        row = card.roles[WORKER]
        assert row.submissions == 2 and row.accepted_work == 1 and row.repeated == 1
        assert not any("refused" in e for e in row.evidence)
        assert sum(role.reviews_changed for role in card.roles.values()) == 0
    finally:
        orchestrator.close()


def test_first_accepted_canonical_result_gets_credit_after_a_missing_file_refusal(tmp_path: Path) -> None:
    company = Company.create(tmp_path / "company", name="Test")
    attempts = 0

    def override(invocation: Any, state: Any, org: Org) -> dict[str, Any] | None:
        nonlocal attempts
        if invocation.purpose == Purpose.DISPATCH:
            payload = default_payload(invocation, state, org)
            payload["tasks"][0]["output_type"] = "TaskReport"
            return payload
        if invocation.purpose == Purpose.EXECUTE:
            attempts += 1
            if attempts == 2:
                path = company.root / ".trypro" / "runs" / state.run_id / "workspace" / "deliverables" / "result.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Delivered after refusal", encoding="utf-8")
            payload = {**default_payload(invocation, state, org), "outputs": ["deliverables/result.txt"]}
            return parse_payload("TaskReport", payload).model_dump(mode="json")
        return None

    result = company.run("Build a service", client=ScriptedClient(override))
    assert result.status == "done" and attempts == 2
    home = company.root / ".trypro"
    records = read_records(home / "runs" / result.run_id / "audit.jsonl")
    worker_records = [r for r in records if r["data"].get("actor") == WORKER]
    submissions = [r["data"]["payload"] for r in worker_records if r["data"].get("kind") == "artifact_submitted"]
    assert len(submissions) == 2 and submissions[0] == submissions[1]
    assert any(r["kind"] == "rejected" for r in records)
    assert any(r["kind"] == "submission_accepted" for r in records)
    row = scorecard(home, company.org).roles[WORKER]
    assert (row.submissions, row.accepted_work, row.changed, row.repeated) == (2, 1, 1, 1)
    assert any("refused" in e for e in row.evidence)


def test_reduction_without_audit_evidence_cannot_be_submitted() -> None:
    with pytest.raises(ValidationError, match="scorecard_evidence"):
        OrgChange.model_validate({**_change(), "scorecard_evidence": []})


def test_ceo_sees_the_scorecard_and_its_proposal_preserves_measured_evidence(tmp_path: Path) -> None:
    company = Company.create(tmp_path / "company", name="Test")
    broken = company.root / ".trypro" / "runs" / "run-corrupt" / "audit.jsonl.gz"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"not gzip")

    def override(invocation: Any, state: Any, org: Org) -> dict[str, Any] | None:
        if invocation.purpose == Purpose.RETROSPECTIVE:
            return {"lessons": [], "org_changes": [_change()]}
        return None

    result = company.run("Build a service", client=ScriptedClient(override))
    assert result.status == "done"
    [proposal] = company.org_proposals()
    assert company.org.has_role(WORKER), "a CEO's proposal removes nothing"
    assert any("audit.jsonl#" in e for e in proposal.change["scorecard_evidence"])
    assert "Role scorecard" in proposal.change["scorecard_evidence"][0]
    assert any("run-corrupt excluded" in e for e in proposal.change["scorecard_evidence"])
    orchestrator = company._orchestrator()
    try:
        retro = Invocation(
            id="retro", role=company.org.root_role, purpose=Purpose.RETROSPECTIVE, output_type="Retrospective"
        )
        prompt = user_prompt(
            retro, orchestrator.state(result.run_id), company.org, briefing=orchestrator.briefing(result.run_id, retro)
        )
        assert "Role scorecard" in prompt and "Model tokens (share)" in prompt
        assert "run-corrupt excluded" in prompt
        assert '<untrusted kind="record">' in prompt
        for status in ("merged", "posted"):
            item = orchestrator.track_outcome(result.run_id, WORKER, f"result {status}")
            assert item is not None
            assert orchestrator.settle_outcome(item.id, status, by="owner") is not None
        assert orchestrator.role_scorecard().roles[WORKER].outcomes == 2
    finally:
        orchestrator.close()


def test_cli_prints_the_same_scorecard_and_the_person_can_accept_a_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    company = Company.create(tmp_path / "company", name="Test")
    monkeypatch.chdir(company.root)
    company.run("Build a service", dry_run=True)
    assert main(["org", "scorecard"]) == 0
    output = capsys.readouterr().out
    assert "Role scorecard from 1 audit log(s)" in output and WORKER in output
    _propose(company, _change())
    monkeypatch.setattr("try_professional.cli._person_at_terminal", lambda: False)
    assert main(["org", "review"]) == 3
    assert company.org.has_role(WORKER)
    monkeypatch.setattr("try_professional.cli._person_at_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "a")
    assert main(["org", "review", "--by", "owner"]) == 0
    assert not company.org.has_role(WORKER)
    assert "retired" in capsys.readouterr().out
