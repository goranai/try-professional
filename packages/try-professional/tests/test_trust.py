"""Trust earned by reviewed work: a role whose recent hand-ins were all approved skips some QA reviews.

A company should get lighter as it goes on, and a solo agent can't: it has no record."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from try_professional.core.artifacts import Flags
from try_professional.core.company import CompanyStore
from try_professional.core.efficiency import measure, trend_line
from try_professional.core.pipeline import ContextResolved, Submission, TaskStatus, advance
from try_professional.core.pipeline.state import FINAL_TASK_ID
from try_professional.org import Org

from .test_pipeline import to_dispatch

ROLE = "engineering/backend"


def _hand_in(
    org: Org, trust: dict[str, int], summary: str = "done", *, feedback: str = "", flags: Flags | None = None
) -> tuple[Any, list[Any]]:
    """A run up to one task's hand-in, with the given trust at its start."""
    state, actions = to_dispatch(org)
    state.trust = dict(trust)
    invoke = next(a for a in actions if a.kind == "invoke_role")
    task = {"id": "t1", "title": "T", "owner_role": ROLE, "goal": "g", "expected_output": "o",
            "definition_of_done": ["d"], "output_type": "CodeChange"}  # fmt: skip
    state, actions = advance(
        state,
        Submission(invocation_id=invoke.invocation_id, actor=invoke.role,
                   payload={"work_package_id": "wp1", "tasks": [task]}),
        org,
    )  # fmt: skip
    state, actions = advance(state, ContextResolved(task_id="wp1/t1", query="T"), org)
    if feedback:
        state.tasks["wp1/t1"].feedback.append(feedback)
    execute = next(a for a in actions if a.kind == "invoke_role")
    return advance(
        state,
        Submission(invocation_id=execute.invocation_id, actor=execute.role, flags=flags or Flags(),
                   payload={"summary": summary, "done_criteria_met": [{"criterion": "d", "met": True}]}),
        org,
    )  # fmt: skip


def _review(org: Org, state: Any, actions: list[Any], decision: str) -> Any:
    review = next(a for a in actions if a.kind == "invoke_role" and a.gate == "qa")
    issues = [] if decision == "approve" else [{"severity": "major", "description": "Wrong"}]
    payload = {"decision": decision, "issues": issues}  # code fills in which work and which gate
    state, _ = advance(state, Submission(invocation_id=review.invocation_id, actor=review.role, payload=payload), org)
    return state


class TestSkipping:
    def test_a_role_without_a_record_is_reviewed(self, org: Org) -> None:
        state, actions = _hand_in(org, {})
        assert state.tasks["wp1/t1"].status == TaskStatus.IN_REVIEW
        assert any(a.kind == "invoke_role" and a.gate == "qa" for a in actions)

    def test_a_trusted_role_skips_the_review(self, org: Org) -> None:
        state, actions = _hand_in(org, {ROLE: 0})
        assert state.tasks["wp1/t1"].status == TaskStatus.APPROVED
        assert not any(a.kind == "invoke_role" and a.gate == "qa" for a in actions)
        assert state.waivers[-1]["decided_by"] == "trust" and state.waivers[-1]["gate"] == "qa"
        assert state.trust[ROLE] == 1

    def test_every_few_hand_ins_one_is_still_reviewed(self, org: Org) -> None:
        every = org.pipeline.spot_check_every
        state, _ = _hand_in(org, {ROLE: every - 1})
        assert state.tasks["wp1/t1"].status == TaskStatus.IN_REVIEW

    def test_anything_flagged_is_reviewed(self, org: Org) -> None:
        state, _ = _hand_in(org, {ROLE: 0}, summary="Stores each customer's email and phone number")
        assert state.tasks["wp1/t1"].status == TaskStatus.IN_REVIEW
        state, _ = _hand_in(org, {ROLE: 0}, flags=Flags(public_facing=True))
        assert state.tasks["wp1/t1"].status == TaskStatus.IN_REVIEW, "a flag the role declared counts too"

    def test_work_sent_back_once_is_reviewed(self, org: Org) -> None:
        state, _ = _hand_in(org, {ROLE: 0}, feedback="qa requested changes: Wrong")
        assert state.tasks["wp1/t1"].status == TaskStatus.IN_REVIEW

    def test_an_approved_spot_check_keeps_trust_and_changes_end_it(self, org: Org) -> None:
        every = org.pipeline.spot_check_every
        state, actions = _hand_in(org, {ROLE: every - 1})
        assert _review(org, state, actions, "approve").trust[ROLE] == 0
        state, actions = _hand_in(org, {ROLE: every - 1})
        assert ROLE not in _review(org, state, actions, "changes_requested").trust


class TestRecord:
    def _store(self, tmp_path: Path, outcomes: list[str], role: str = ROLE) -> CompanyStore:
        store = CompanyStore(tmp_path / "company.db")
        with store._conn:
            for number, outcome in enumerate(outcomes):
                store._conn.execute(
                    "INSERT INTO reviews VALUES (?, ?, ?, ?, ?)",
                    (f"r:{number}", "r", role, f"2026-11-11T00:00:{number:02d}", outcome),
                )
        return store

    def test_five_clean_reviews_earn_trust(self, tmp_path: Path) -> None:
        assert self._store(tmp_path, ["clean"] * 5).trust(5) == {ROLE: 0}

    def test_fewer_or_one_with_changes_dont(self, tmp_path: Path) -> None:
        assert self._store(tmp_path / "a", ["clean"] * 4).trust(5) == {}
        assert self._store(tmp_path / "b", ["clean", "changes", *["clean"] * 4]).trust(5) == {}

    def test_skips_since_the_last_review_are_counted(self, tmp_path: Path) -> None:
        assert self._store(tmp_path, [*["clean"] * 5, "skipped", "skipped"]).trust(5) == {ROLE: 2}

    def test_zero_turns_it_off(self, tmp_path: Path) -> None:
        assert self._store(tmp_path, ["clean"] * 5).trust(0) == {}

    def test_a_rebuild_starts_the_record_over(self, tmp_path: Path, org: Org) -> None:
        store = self._store(tmp_path, ["clean"] * 5)
        store.rebuild([], org, "2026-11-11T00:00:00")
        assert store.trust(5) == {}

    def test_a_skip_is_recorded_where_its_work_was_handed_in(self, org: Org) -> None:
        """Skip, skip, then a spot check that passed: the record ends on the review, so the next run starts at 0."""
        from try_professional.core.company import _reviews

        every = org.pipeline.spot_check_every
        state, actions = _hand_in(org, {ROLE: every - 1})
        state = _review(org, state, actions, "approve")
        state.waivers.insert(0, {"task_id": "wp1/t1", "gate": "qa", "note": "", "decided_by": "trust"})
        assert [outcome for _, _, outcome in _reviews(state)] == ["skipped", "clean"]


class TestOverRuns:
    def test_a_company_stops_reviewing_work_that_keeps_passing(self, org: Org, harness: Callable[..., Any]) -> None:
        """Real runs through the whole pipeline: reviews are recorded, trust is earned, and a later run skips."""
        run = harness(org)
        skipped, runs = [], []
        for _ in range(org.pipeline.trust_after + 2):
            result = run.runner.run("Goal")
            assert result.status == "done"
            state = run.orchestrator.state(result.run_id)
            runs.append(result.run_id)
            skipped.append(sum(1 for w in state.waivers if w["decided_by"] == "trust"))
        assert skipped[0] == 0 and sum(skipped) > 0
        audit = run.orchestrator.audit_path(result.run_id).read_text(encoding="utf-8")
        assert '"trusted"' in audit
        # Only plain work's QA review is ever skipped: never a final deliverable, never another gate.
        state = run.orchestrator.state(result.run_id)
        assert all(w["gate"] == "qa" and w["task_id"] != FINAL_TASK_ID
                   for w in state.waivers if w["decided_by"] == "trust")  # fmt: skip
        skipping_run = next(r for r, n in zip(runs, skipped, strict=True) if n)
        lines = measure(run.orchestrator.audit_path(skipping_run), skipping_run).lines()
        assert any(line.startswith("Reviews skipped") for line in lines)


class TestTrend:
    def test_cost_per_task_says_which_way_it_went(self) -> None:
        assert trend_line([120_000]) is None
        line = trend_line([120_000, 100_000, 90_000])
        assert line is not None and "120k, 100k, 90k" in line and "25% lighter" in line
        assert "heavier" in (trend_line([90_000, 120_000]) or "")

    def test_the_company_reports_tokens_per_task(
        self, org: Org, harness: Callable[..., Any], capsys: pytest.CaptureFixture[str]
    ) -> None:
        from try_professional.adapters.mcp import McpService
        from try_professional.cli import _print_efficiency

        run = harness(org)
        for _ in range(2):
            result = run.runner.run("Goal")
        assert len(run.orchestrator.company.tokens_per_task()) == 2
        _print_efficiency(run.orchestrator, result.run_id)
        assert "Tokens per task over the last 2 finished commands" in capsys.readouterr().out
        finished = McpService(run.orchestrator, coordinator=True).run_next_action(result.run_id)
        assert finished["cost_per_task"].startswith("Tokens per task over the last 2")
