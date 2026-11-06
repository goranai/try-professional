"""Per-role skill matching: who gets what, who decides, and what use teaches us."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from skill_customs import Lockfile, approve, pin_local
from try_professional.adapters.api_runner.clients import ChatRequest, ChatResponse, ToolCall
from try_professional.adapters.api_runner.runner import ApiRunner
from try_professional.adapters.api_runner.scripted import ScriptedClient
from try_professional.adapters.api_runner.tools import ToolContext, ToolRefused, run_tool
from try_professional.cli import main
from try_professional.core.orchestrator import Orchestrator
from try_professional.core.pipeline import Purpose
from try_professional.core.ports import NullMemory
from try_professional.org import Org
from try_professional.skills import (
    GAP_THRESHOLD,
    UNCATEGORIZED,
    Decisions,
    LoadoutStore,
    LockedSkills,
    RoleLoadout,
    SkillCatalog,
    SkillRecord,
    compute_loadouts,
    exclusion_reason,
    match_all,
    normalize_tool,
    records_from_lock,
    sync_catalog,
)

from .conftest import ROUTES
from .test_memory import FakeEmbedder

BACKEND = "engineering/backend"
WRITER = "marketing/content-writer"
LEAD = "engineering/tech-lead"
CEO = "executive/ceo"
# Wording taken from the roles' own skill_needs, so a bag-of-words embedder scores these highly.
BACKEND_TEXT = "design and build REST APIs"
WRITER_TEXT = "write blog posts and articles"


def _record(skill_id: str, description: str = BACKEND_TEXT, **kwargs: Any) -> SkillRecord:
    defaults: dict[str, Any] = {"name": skill_id, "category": "backend", "content_sha256": f"hash-{skill_id}"}
    return SkillRecord(skill_id=skill_id, description=description, **{**defaults, **kwargs})


def _no_usage(role: str, skill_id: str) -> dict[str, int]:
    return {"uses": 0, "passed": 0, "failed": 0, "idle_runs": 0}


def _compute(
    org: Org, records: list[SkillRecord], decisions: Decisions, usage: Any = _no_usage, threshold: float = 0.3
) -> dict[str, RoleLoadout]:
    embedder = FakeEmbedder()
    vectors = embedder.embed_documents([r.search_text for r in records])
    return compute_loadouts(
        org, list(zip(records, vectors, strict=True)), embedder, decisions, usage, threshold=threshold
    )


class TestEligibility:
    @pytest.fixture
    def role(self, org: Org) -> Any:
        return org.role(BACKEND)

    def test_a_plain_skill_is_eligible(self, role: Any) -> None:
        assert exclusion_reason(role, _record("a")) is None

    def test_quarantined_is_never_eligible(self, role: Any) -> None:
        assert "quarantined" in (exclusion_reason(role, _record("a", trust="quarantined")) or "")

    def test_uncategorized_is_never_in_a_loadout(self, role: Any) -> None:
        assert "category" in (exclusion_reason(role, _record("a", category=UNCATEGORIZED)) or "")

    def test_a_skill_needing_a_tool_the_role_lacks_is_excluded(self, role: Any) -> None:
        assert "shell" in (exclusion_reason(role, _record("a", tools=("Bash",))) or "")

    def test_tools_the_role_has_are_fine(self, role: Any) -> None:
        assert exclusion_reason(role, _record("a", tools=("Read", "Write"))) is None

    def test_an_unknown_tool_counts_as_one_the_role_lacks(self, role: Any) -> None:
        assert "SomeMysteryTool" in (exclusion_reason(role, _record("a", tools=("SomeMysteryTool",))) or "")

    def test_forbidden_tools_exclude_even_with_the_tool_otherwise_available(self, org: Org) -> None:
        reason = exclusion_reason(org.role("engineering/qa"), _record("a", tools=("Write",)))
        assert "forbidden" in (reason or "")

    def test_scoped_tool_names_are_understood(self) -> None:
        assert normalize_tool("Bash(git status:*)") == "shell"
        assert normalize_tool("WebFetch") == "network"
        assert normalize_tool("workspace_read") == "workspace_read"

    def test_every_default_role_has_a_skill_profile(self, org: Org) -> None:
        for role in org.roles.values():
            assert role.skill_needs, role.ref
            assert role.max_skills == 8
            assert set(role.required_tools) <= set(role.allowed_tools), role.ref


class TestMatching:
    def test_a_role_matches_skills_like_its_needs(self, org: Org, tmp_path: Path) -> None:
        records = [_record("api"), _record("blog", WRITER_TEXT, category="content")]
        loadouts = _compute(org, records, Decisions(tmp_path / "d.yaml"), threshold=0.5)
        assert "api" in {m.record.skill_id for m in loadouts[BACKEND].pending}
        assert "blog" in {m.record.skill_id for m in loadouts[WRITER].pending}
        assert "blog" not in {m.record.skill_id for m in loadouts[BACKEND].pending}

    def test_nothing_is_active_until_a_person_admits_it(self, org: Org, tmp_path: Path) -> None:
        loadouts = _compute(org, [_record("api")], Decisions(tmp_path / "d.yaml"))
        assert loadouts[BACKEND].active == []
        assert [m.record.skill_id for m in loadouts[BACKEND].pending] == ["api"]

    def test_admission_makes_it_active(self, org: Org, tmp_path: Path) -> None:
        decisions = Decisions(tmp_path / "d.yaml")
        record = _record("api")
        decisions.decide(BACKEND, "api", "approved", by="Rina", content_sha256=record.content_sha256)
        loadouts = _compute(org, [record], decisions)
        assert [m.record.skill_id for m in loadouts[BACKEND].active] == ["api"]
        assert loadouts[BACKEND].pending == []

    def test_a_changed_skill_goes_back_to_waiting(self, org: Org, tmp_path: Path) -> None:
        decisions = Decisions(tmp_path / "d.yaml")
        decisions.decide(BACKEND, "api", "approved", by="Rina", content_sha256="old-hash")
        loadouts = _compute(org, [_record("api", content_sha256="new-hash")], decisions)
        assert loadouts[BACKEND].active == []
        assert [m.record.skill_id for m in loadouts[BACKEND].pending] == ["api"]

    def test_a_rejected_skill_stays_out_across_rematches(self, org: Org, tmp_path: Path) -> None:
        decisions = Decisions(tmp_path / "d.yaml")
        record = _record("api")
        decisions.decide(BACKEND, "api", "rejected", by="Rina", content_sha256=record.content_sha256)
        loadouts = _compute(org, [record], decisions)
        assert loadouts[BACKEND].pending == [] and "rejected" in loadouts[BACKEND].excluded["api"]

    def test_the_cap_holds_and_extras_are_reported(self, org: Org, tmp_path: Path) -> None:
        decisions = Decisions(tmp_path / "d.yaml")
        records = [_record(f"s{n}", f"{BACKEND_TEXT} variant{n} other{n} words{n} more{n}") for n in range(12)]
        for record in records:
            decisions.decide(BACKEND, record.skill_id, "approved", by="R", content_sha256=record.content_sha256)
        loadout = _compute(org, records, decisions, threshold=0.0)[BACKEND]
        assert len(loadout.active) == org.role(BACKEND).max_skills
        assert len(loadout.active) + len(loadout.over_cap) + len(loadout.collapsed) == 12

    def test_near_duplicates_collapse_into_the_better_match(self, org: Org, tmp_path: Path) -> None:
        records = [
            _record("a", name="api"),
            _record("b", name="api"),
            _record("other", f"{BACKEND_TEXT} entirely different extra words here"),
        ]
        loadout = _compute(org, records, Decisions(tmp_path / "d.yaml"), threshold=0.0)[BACKEND]
        kept = {m.record.skill_id for m in loadout.pending}
        assert "a" in loadout.collapsed or "b" in loadout.collapsed
        assert len(kept & {"a", "b"}) == 1 and "other" in kept

    def test_the_approved_copy_of_a_duplicate_wins(self, org: Org, tmp_path: Path) -> None:
        decisions = Decisions(tmp_path / "d.yaml")
        decisions.decide(BACKEND, "b", "approved", by="R", content_sha256="hash-b")
        records = [_record("a", name="api"), _record("b", name="api")]
        loadout = _compute(org, records, decisions, threshold=0.0)[BACKEND]
        assert [m.record.skill_id for m in loadout.active] == ["b"]
        assert loadout.collapsed == {"a": "b"}

    def test_why_explains_the_match(self, org: Org, tmp_path: Path) -> None:
        match = _compute(org, [_record("api")], Decisions(tmp_path / "d.yaml"))[BACKEND].pending[0]
        assert BACKEND_TEXT in match.why and "backend is allowed" in match.why

    def test_category_fit_raises_the_score(self, org: Org, tmp_path: Path) -> None:
        fits = _record("fits", category="backend")
        other = _record("other", category="astrology")
        scores = {
            m.record.skill_id: m.score
            for m in _compute(org, [fits], Decisions(tmp_path / "a.yaml"))[BACKEND].pending
            + _compute(org, [other], Decisions(tmp_path / "b.yaml"))[BACKEND].pending
        }
        assert scores["fits"] > scores["other"]

    def test_good_results_raise_a_skill_and_bad_ones_lower_it(self, org: Org, tmp_path: Path) -> None:
        def usage(passed: int, failed: int) -> Any:
            return lambda role, skill: {"uses": passed + failed, "passed": passed, "failed": failed, "idle_runs": 0}

        record = _record("api")
        base = _compute(org, [record], Decisions(tmp_path / "d.yaml"))[BACKEND].pending[0].score
        good = _compute(org, [record], Decisions(tmp_path / "d.yaml"), usage(5, 0))[BACKEND].pending[0]
        bad = _compute(org, [record], Decisions(tmp_path / "d.yaml"), usage(0, 5))[BACKEND].pending[0]
        assert bad.score < base < good.score
        assert "used 5x, 5 passed QA" in good.why

    def test_a_few_results_change_nothing_yet(self, org: Org, tmp_path: Path) -> None:
        record = _record("api")
        base = _compute(org, [record], Decisions(tmp_path / "d.yaml"))[BACKEND].pending[0].score
        small = _compute(
            org,
            [record],
            Decisions(tmp_path / "d.yaml"),
            lambda r, s: {"uses": 1, "passed": 1, "failed": 0, "idle_runs": 0},
        )
        assert small[BACKEND].pending[0].score == base


class TestDecisionsFile:
    def test_round_trip_is_reviewable_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "skills" / "decisions.yaml"
        decisions = Decisions(path)
        decisions.decide(BACKEND, "api", "approved", by="Rina", content_sha256="h")
        decisions.assign_category("api", "backend", by="Rina")
        decisions.save()
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert document["loadouts"][BACKEND]["api"]["by"] == "Rina"
        reloaded = Decisions(path)
        assert reloaded.category_for("api") == "backend"
        assert reloaded.status(BACKEND, _record("api", content_sha256="h")) == "approved"

    def test_an_unknown_version_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "decisions.yaml"
        path.write_text("version: 99\n", encoding="utf-8")
        with pytest.raises(ValueError, match="version"):
            Decisions(path)


class TestFeedbackStore:
    @pytest.fixture
    def store(self, tmp_path: Path) -> Iterator[LoadoutStore]:
        built = LoadoutStore(tmp_path / "l.sqlite")
        yield built
        built.close()

    def test_a_use_counts_once_per_task(self, store: LoadoutStore) -> None:
        store.record_use("r", "t", BACKEND, "api")
        store.record_use("r", "t", BACKEND, "api")
        store.record_use("r", "t2", BACKEND, "api")
        assert store.usage(BACKEND, "api")["uses"] == 2

    def test_the_latest_verdict_decides(self, store: LoadoutStore) -> None:
        store.record_use("r", "t", BACKEND, "api")
        store.record_outcome("r", "t", passed=False)
        assert store.usage(BACKEND, "api")["failed"] == 1
        store.record_outcome("r", "t", passed=True)
        usage = store.usage(BACKEND, "api")
        assert (usage["passed"], usage["failed"]) == (1, 0), "rework that ends in approval is a pass"

    def test_outcomes_only_touch_that_tasks_skills(self, store: LoadoutStore) -> None:
        store.record_use("r", "t", BACKEND, "api")
        store.record_use("r", "t2", BACKEND, "other")
        store.record_outcome("r", "t", passed=True)
        assert store.usage(BACKEND, "other")["passed"] == 0

    def _activate(self, store: LoadoutStore, org: Org, tmp_path: Path) -> None:
        decisions = Decisions(tmp_path / "x.yaml")
        decisions.decide(BACKEND, "api", "approved", by="R", content_sha256="hash-api")
        store.replace_matches(_compute(org, [_record("api")], decisions))

    def test_idle_runs_count_up_and_reset_on_use(self, store: LoadoutStore, org: Org, tmp_path: Path) -> None:
        self._activate(store, org, tmp_path)
        for n in range(3):
            store.record_run(f"r{n}", [BACKEND])
        assert store.usage(BACKEND, "api")["idle_runs"] == 3
        store.record_use("r9", "t", BACKEND, "api")
        store.record_run("r9", [BACKEND])
        assert store.usage(BACKEND, "api")["idle_runs"] == 0

    def test_runs_where_the_role_did_nothing_do_not_count(self, store: LoadoutStore, org: Org, tmp_path: Path) -> None:
        self._activate(store, org, tmp_path)
        store.record_run("r1", [WRITER])
        assert store.usage(BACKEND, "api")["idle_runs"] == 0

    def test_long_unused_skills_are_flagged(self, store: LoadoutStore, org: Org, tmp_path: Path) -> None:
        self._activate(store, org, tmp_path)
        for n in range(5):
            store.record_run(f"r{n}", [BACKEND])
        assert store.unused(6) == []
        assert store.unused(5) == [{"role": BACKEND, "skill_id": "api", "idle_runs": 5}]

    def test_repeat_gaps_are_counted_not_duplicated(self, store: LoadoutStore) -> None:
        store.record_gap(BACKEND, "  migrate   the database ", 0.1, run_id="r", task_id="t")
        store.record_gap(BACKEND, "migrate the database", 0.2, run_id="r2", task_id="t2")
        gaps = store.gaps()
        assert len(gaps) == 1 and gaps[0]["count"] == 2 and gaps[0]["description"] == "migrate the database"


@dataclass
class World:
    org: Org
    catalog: SkillCatalog
    store: LoadoutStore
    decisions: Decisions
    lock: Lockfile
    dest: Path
    skills: LockedSkills

    def rematch(self) -> None:
        sync_catalog(self.catalog, self.lock, self.dest, self.decisions)
        match_all(self.org, self.catalog, self.store, self.decisions)

    def admit(self, role: str, skill_id: str) -> None:
        entry = self.lock.entries[skill_id]
        self.decisions.decide(role, skill_id, "approved", by="Rina", content_sha256=entry.content_sha256)
        self.rematch()


def _skill_file(folder: Path, description: str, category: str | None, tools: str | None = None) -> Path:
    folder.mkdir(parents=True)
    lines = [f"name: {folder.name}", f"description: {description}", "license: MIT"]
    if category:
        lines.append(f"category: {category}")
    if tools:
        lines.append(f"allowed-tools: {tools}")
    (folder / "SKILL.md").write_text(
        "---\n" + "\n".join(lines) + f"\n---\nFull text of {folder.name}.\n", encoding="utf-8"
    )
    return folder


@pytest.fixture
def world(tmp_path: Path, org: Org) -> Iterator[World]:
    dest = tmp_path / "skills"
    lock = Lockfile(tmp_path / "skills.lock")
    for name, description, category, verified in (
        ("api", BACKEND_TEXT, "backend", True),
        ("blog", WRITER_TEXT, "content", True),
        ("mystery", "Something unlabelled that does things", None, True),
        ("waiting", "write database schemas and migrations", "databases", False),
    ):
        result = pin_local(_skill_file(tmp_path / "src" / name, description, category), license_spdx="MIT", dest=dest)
        lock.add(result.entry)
        if verified:
            approve(lock, result.entry.skill_id, by="Rina", dest=dest)
    catalog = SkillCatalog(tmp_path / "catalog.sqlite", FakeEmbedder())
    store = LoadoutStore(tmp_path / "loadouts.sqlite")
    decisions = Decisions(tmp_path / "skills" / "decisions.yaml")
    built = World(org, catalog, store, decisions, lock, dest, LockedSkills(org, catalog, dest, lock, store))
    built.rematch()
    yield built
    store.close()
    catalog.close()


class TestWhatARoleCanReach:
    def test_nothing_until_a_person_admits_it(self, world: World) -> None:
        assert world.skills.loadout(BACKEND) == []
        assert world.skills.get(BACKEND, "local/api") is None
        assert world.skills.search(BACKEND, "rest apis") == []

    def test_an_admitted_skill_is_reachable(self, world: World) -> None:
        world.admit(BACKEND, "local/api")
        assert [i["skill_id"] for i in world.skills.loadout(BACKEND)] == ["local/api"]
        assert "Full text of api" in (world.skills.get(BACKEND, "local/api") or "")
        assert world.skills.search(BACKEND, "rest apis")[0]["skill_id"] == "local/api"

    def test_another_roles_loadout_is_out_of_reach(self, world: World) -> None:
        world.admit(WRITER, "local/blog")
        assert world.skills.get(BACKEND, "local/blog") is None
        assert world.skills.search(BACKEND, "blog posts") == []

    def test_a_quarantined_skill_cannot_be_reached_even_if_a_decision_names_it(self, world: World) -> None:
        world.decisions.decide(BACKEND, "local/waiting", "approved", by="R", content_sha256="x")
        world.rematch()
        assert world.skills.get(BACKEND, "local/waiting") is None

    def test_the_loadout_holds_short_descriptions_only(self, world: World) -> None:
        world.admit(BACKEND, "local/api")
        item = world.skills.loadout(BACKEND)[0]
        assert set(item) == {"skill_id", "description"}
        assert "Full text" not in str(item)


class TestUncategorized:
    def test_it_is_uncategorized_with_no_declared_category(self, world: World) -> None:
        records = {r.skill_id: r for r in records_from_lock(world.lock, world.dest)}
        assert records["local/mystery"].category == UNCATEGORIZED

    def test_ordinary_roles_cannot_see_it(self, world: World) -> None:
        for role in (BACKEND, "marketing/social", "executive/chief-of-staff"):
            assert world.skills.search(role, "something unlabelled") == [], role
            assert world.skills.get(role, "local/mystery") is None, role
            assert not world.org.is_senior(role)

    @pytest.mark.parametrize("role", [CEO, LEAD, "legal/counsel", "finance/finance-lead"])
    def test_the_ceo_and_heads_see_it_labelled(self, world: World, role: str) -> None:
        found = world.skills.search(role, "something unlabelled")
        assert found[0]["skill_id"] == "local/mystery" and found[0]["category"] == UNCATEGORIZED
        assert (world.skills.get(role, "local/mystery") or "").startswith("[uncategorized")

    def test_no_loadout_ever_holds_it(self, world: World) -> None:
        loadouts = match_all(world.org, world.catalog, world.store, world.decisions)
        for loadout in loadouts.values():
            assert "local/mystery" not in {m.record.skill_id for m in [*loadout.active, *loadout.pending]}

    def test_a_person_assigning_a_category_ends_it(self, world: World) -> None:
        world.decisions.assign_category("local/mystery", "backend", by="Rina")
        world.rematch()
        assert world.catalog.record("local/mystery").category == "backend"  # type: ignore[union-attr]
        assert world.skills.get(LEAD, "local/mystery") is None, "now it needs admitting like any other"
        world.admit(BACKEND, "local/mystery")
        assert world.skills.get(BACKEND, "local/mystery")


class TestProposals:
    def test_a_head_can_propose_for_its_own_department(self, world: World) -> None:
        world.skills.propose(LEAD, "local/api", BACKEND, "We build APIs")
        assert world.store.proposals(BACKEND)[0]["proposed_by"] == LEAD

    def test_proposing_changes_nothing(self, world: World) -> None:
        world.skills.propose(LEAD, "local/api", BACKEND, "We build APIs")
        world.rematch()
        assert world.skills.loadout(BACKEND) == []
        assert world.skills.get(BACKEND, "local/api") is None

    def test_proposing_never_touches_the_decisions_file(self, world: World) -> None:
        before = world.decisions.path.exists()
        world.skills.propose(LEAD, "local/api", BACKEND, "We build APIs")
        assert world.decisions.path.exists() == before

    def test_a_head_cannot_propose_for_another_department(self, world: World) -> None:
        with pytest.raises(PermissionError, match="own department"):
            world.skills.propose(LEAD, "local/blog", WRITER, "Nice")

    def test_the_ceo_can_propose_for_anyone(self, world: World) -> None:
        world.skills.propose(CEO, "local/blog", WRITER, "Nice")
        assert world.store.proposals(WRITER)

    def test_ordinary_roles_cannot_propose(self, world: World) -> None:
        with pytest.raises(PermissionError, match="can't propose"):
            world.skills.propose(BACKEND, "local/api", BACKEND, "Please")

    def test_quarantined_skills_cannot_be_proposed(self, world: World) -> None:
        with pytest.raises(PermissionError, match="isn't available"):
            world.skills.propose(LEAD, "local/waiting", BACKEND, "Please")

    def test_the_tool_turns_refusals_into_tool_errors(self, world: World, tmp_path: Path) -> None:
        context = ToolContext(BACKEND, ("skill_propose",), tmp_path, NullMemory(), world.skills)
        with pytest.raises(ToolRefused, match="can't propose"):
            run_tool(context, "skill_propose", {"skill_id": "local/api", "for_role": BACKEND, "reason": "x"})

    def test_only_senior_roles_are_granted_the_tool(self, org: Org) -> None:
        for ref, role in org.roles.items():
            assert ("skill_propose" in role.allowed_tools) == org.is_senior(ref), ref

    def test_no_agent_tool_can_admit_or_categorize(self) -> None:
        from try_professional.adapters.api_runner import tools

        for name in tools._SPECS:
            assert not any(word in name for word in ("admit", "approve", "categorize", "reject", "decide"))


class _UsesSkill(ScriptedClient):
    """A scripted model that reads one skill before it does the work."""

    def __init__(self, skill_id: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.skill_id = skill_id
        self.done = False

    def complete(self, request: ChatRequest) -> ChatResponse:
        invocation = request.metadata["invocation"]
        if (
            invocation.purpose == Purpose.EXECUTE
            and not self.done
            and any(t.name == "skill_get" for t in request.tools)
        ):
            self.done = True
            call = ToolCall(id="c1", name="skill_get", arguments={"skill_id": self.skill_id})
            return ChatResponse(
                text="",
                tool_calls=[call],
                stop_reason="tool_use",
                assistant_message={},
                input_tokens=1,
                output_tokens=1,
            )
        return super().complete(request)


class TestInARun:
    def _run(self, world: World, tmp_path: Path, client: Any) -> Any:
        orchestrator = Orchestrator(world.org, tmp_path / "home", skills=world.skills, feedback=world.skills)
        runner = ApiRunner(orchestrator, client, ROUTES)
        return orchestrator, runner.run("rest apis for orders")

    def test_the_prompt_lists_names_and_short_descriptions_only(self, world: World, tmp_path: Path) -> None:
        world.admit(BACKEND, "local/api")
        prompts: list[str] = []

        class Capture(ScriptedClient):
            def complete(self, request: ChatRequest) -> ChatResponse:
                if request.metadata["invocation"].purpose == Purpose.EXECUTE:
                    prompts.append(request.messages[0]["content"])
                return super().complete(request)

        _, result = self._run(world, tmp_path, Capture())
        assert result.status == "done"
        assert "local/api" in prompts[0] and BACKEND_TEXT in prompts[0]
        assert "Full text of api" not in prompts[0], "the full skill is fetched on demand"
        assert '<untrusted kind="skill-list">' in prompts[0], "third parties wrote these descriptions"

    def test_a_used_skill_that_passes_qa_is_recorded(self, world: World, tmp_path: Path) -> None:
        world.admit(BACKEND, "local/api")
        _, result = self._run(world, tmp_path, _UsesSkill("local/api"))
        assert result.status == "done"
        usage = world.store.usage(BACKEND, "local/api")
        assert (usage["uses"], usage["passed"], usage["failed"]) == (1, 1, 0)

    def test_a_use_is_in_the_audit_log(self, world: World, tmp_path: Path) -> None:
        from try_professional.core.audit import read_records

        world.admit(BACKEND, "local/api")
        orchestrator, result = self._run(world, tmp_path, _UsesSkill("local/api"))
        records = read_records(orchestrator.audit_path(result.run_id))
        assert any(r["kind"] == "skill_used" and r["data"]["skill_id"] == "local/api" for r in records)

    def test_failing_qa_then_passing_counts_as_a_pass(self, world: World, tmp_path: Path) -> None:
        world.admit(BACKEND, "local/api")
        verdicts = ["changes_requested", "approve"]

        def override(invocation: Any, state: Any, org: Org) -> dict[str, Any] | None:
            if invocation.purpose == Purpose.REVIEW and invocation.gate == "qa" and verdicts:
                decision = verdicts.pop(0)
                issues = [{"severity": "major", "description": "x"}] if decision != "approve" else []
                return {
                    "gate": "qa",
                    "subject_artifact_id": invocation.subject_artifact_id,
                    "decision": decision,
                    "issues": issues,
                }
            return None

        _, result = self._run(world, tmp_path, _UsesSkill("local/api", override=override))
        assert result.status == "done"
        usage = world.store.usage(BACKEND, "local/api")
        assert (usage["passed"], usage["failed"]) == (1, 0)

    def test_a_task_with_no_fitting_skill_logs_a_gap(self, world: World, tmp_path: Path) -> None:
        _, result = self._run(world, tmp_path, ScriptedClient())
        assert result.status == "done"
        gaps = world.store.gaps()
        assert gaps and gaps[0]["role"] == BACKEND and gaps[0]["best_score"] < GAP_THRESHOLD
        assert "rest apis" in gaps[0]["description"]

    def test_a_role_with_no_skills_is_not_offered_skill_tools(self, world: World, tmp_path: Path) -> None:
        client = ScriptedClient()
        self._run(world, tmp_path, client)
        execute = next(c for c in client.calls if c["purpose"] == "execute")
        assert "skill_get" not in execute["tools"] and "skill_search" not in execute["tools"]

    def test_finishing_a_run_counts_idle_skills(self, world: World, tmp_path: Path) -> None:
        world.admit(BACKEND, "local/api")
        _, result = self._run(world, tmp_path, ScriptedClient())
        assert result.status == "done"
        assert world.store.usage(BACKEND, "local/api")["idle_runs"] == 1

    def test_a_feedback_failure_does_not_stop_a_run(self, world: World, tmp_path: Path) -> None:
        class Broken:
            def __getattr__(self, name: str) -> Any:
                def fail(*args: Any, **kwargs: Any) -> None:
                    raise RuntimeError("database locked")

                return fail

        orchestrator = Orchestrator(world.org, tmp_path / "home", skills=world.skills, feedback=Broken())  # type: ignore[arg-type]
        assert ApiRunner(orchestrator, ScriptedClient(), ROUTES).run("Goal").status == "done"


class TestPromptBudget:
    """Loadouts must fit the same budget as a run with no skills."""

    def _measure(self) -> Any:
        root = Path(__file__).resolve().parents[3]
        spec = importlib.util.spec_from_file_location("measure_prompts", root / "scripts" / "measure_prompts.py")
        assert spec and spec.loader
        module: Any = importlib.util.module_from_spec(spec)
        sys.modules["measure_prompts"] = module
        spec.loader.exec_module(module)
        return module

    def test_a_full_loadout_stays_within_budget(self) -> None:
        from .test_token_budget import TOTAL_CHAR_BUDGET

        steps = self._measure().measure(loadout_size=8)
        assert sum(s.total for s in steps) <= TOTAL_CHAR_BUDGET

    def test_it_really_was_a_full_loadout(self) -> None:
        module = self._measure()
        with_skills = module.measure(loadout_size=8)
        without = module.measure()
        execute = next(s for s in with_skills if s.purpose == "execute")
        plain = next(s for s in without if s.purpose == "execute")
        assert execute.total > plain.total + 8 * 40


class TestCommands:
    @pytest.fixture
    def project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("try_professional.cli.FastEmbedEmbedder", FakeEmbedder)
        assert main(["init", "--skip-model"]) == 0
        dest = tmp_path / "skills"
        lock = Lockfile(tmp_path / "skills.lock")
        for name, description, category in (("api", BACKEND_TEXT, "backend"), ("mystery", "Unlabelled", None)):
            result = pin_local(
                _skill_file(tmp_path / "src" / name, description, category), license_spdx="MIT", dest=dest
            )
            lock.add(result.entry)
            approve(lock, result.entry.skill_id, by="Rina", dest=dest)
        lock.save()
        return tmp_path

    def _person(self, monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
        monkeypatch.setattr("builtins.input", lambda _: answer)

    def test_match_reports_what_is_waiting(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        capsys.readouterr()
        assert main(["skills", "match"]) == 0
        assert "waiting for a person" in capsys.readouterr().out

    def test_loadout_shows_scores_and_why(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        main(["skills", "match"])
        capsys.readouterr()
        assert main(["skills", "loadout", BACKEND]) == 0
        output = capsys.readouterr().out
        assert "local/api" in output and "fits" in output and "Waiting for a person" in output

    def test_loadout_shows_heads_uncategorized_skills_labelled(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["skills", "match"])
        capsys.readouterr()
        main(["skills", "loadout", LEAD])
        assert "[uncategorized] local/mystery" in capsys.readouterr().out
        main(["skills", "loadout", BACKEND])
        assert "local/mystery" not in capsys.readouterr().out

    def test_an_unknown_role_is_refused(self, project: Path) -> None:
        assert main(["skills", "loadout", "ghost/role"]) == 2

    def test_gaps_lists_nothing_yet(self, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
        capsys.readouterr()
        assert main(["skills", "gaps"]) == 0
        assert "No skill gaps" in capsys.readouterr().out

    def test_gaps_can_search_github_and_install_nothing(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        store = LoadoutStore(project / ".trypro" / "skills" / "loadouts.sqlite")
        store.record_gap(BACKEND, "migrate the database", 0.1, run_id="r", task_id="t")
        store.close()
        monkeypatch.setattr("try_professional.cli.discover", lambda query, top: [])
        capsys.readouterr()
        assert main(["skills", "gaps"]) == 0
        assert "sends those descriptions to GitHub" in capsys.readouterr().out
        before = (project / "skills.lock").read_text(encoding="utf-8")
        assert main(["skills", "gaps", "--discover"]) == 0
        assert "Nothing was installed" in capsys.readouterr().out
        assert (project / "skills.lock").read_text(encoding="utf-8") == before

    @pytest.mark.parametrize(
        "command",
        [
            ["skills", "admit", "local/api", "--by", "An Agent"],
            ["skills", "reject", "local/api", "--role", BACKEND, "--by", "An Agent"],
            ["skills", "categorize", "local/mystery", "backend", "--by", "An Agent"],
        ],
    )
    def test_human_decisions_refuse_to_run_without_a_terminal(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, command: list[str]
    ) -> None:
        main(["skills", "match"])
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("local/api"))
        assert main(command) == 2
        assert not (project / "skills" / "decisions.yaml").exists()

    def test_a_person_admits_then_it_is_active(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["skills", "match"])
        self._person(monkeypatch, "local/api")
        assert main(["skills", "admit", "local/api", "--by", "Rina"]) == 0
        main(["skills", "match"])
        capsys.readouterr()
        main(["skills", "loadout", BACKEND])
        output = capsys.readouterr().out
        assert "Active:" in output and "local/api" in output

    def test_a_wrong_confirmation_changes_nothing(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        main(["skills", "match"])
        self._person(monkeypatch, "wrong")
        assert main(["skills", "admit", "local/api", "--by", "Rina"]) == 1
        assert not (project / "skills" / "decisions.yaml").exists()

    def test_uncategorized_must_get_a_category_before_admission(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["skills", "match"])
        self._person(monkeypatch, "local/mystery")
        capsys.readouterr()
        assert main(["skills", "admit", "local/mystery", "--by", "Rina", "--role", BACKEND]) == 1
        assert "no category" in capsys.readouterr().out
        assert main(["skills", "categorize", "local/mystery", "astrology", "--by", "Rina"]) == 1
        assert main(["skills", "categorize", "local/mystery", "backend", "--by", "Rina"]) == 0
        main(["skills", "match"])
        assert main(["skills", "admit", "local/mystery", "--by", "Rina", "--role", BACKEND]) == 0

    def test_match_refuses_when_files_do_not_match_the_lock(
        self, project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (project / "skills/installed/local/api/SKILL.md").write_text("changed", encoding="utf-8")
        capsys.readouterr()
        assert main(["skills", "match"]) == 1
        assert "Not syncing" in capsys.readouterr().out

    def test_a_long_unused_skill_is_flagged_for_removal(
        self, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        main(["skills", "match"])
        self._person(monkeypatch, "local/api")
        main(["skills", "admit", "local/api", "--by", "Rina"])
        main(["skills", "match"])
        store = LoadoutStore(project / ".trypro" / "skills" / "loadouts.sqlite")
        for n in range(3):
            store.record_run(f"r{n}", [BACKEND])
        store.close()
        capsys.readouterr()
        main(["skills", "match", "--unused-runs", "3"])
        assert "Consider removing: local/api" in capsys.readouterr().out


class TestSkillMetadata:
    def test_declared_tools_are_read_from_the_lock_path(self, tmp_path: Path) -> None:
        dest = tmp_path / "skills"
        lock = Lockfile(tmp_path / "l")
        folder = _skill_file(tmp_path / "src" / "tooly", "d", "backend", "Read Write")
        result = pin_local(folder, license_spdx="MIT", dest=dest)
        lock.add(result.entry)
        assert records_from_lock(lock, dest)[0].tools == ("Read", "Write")

    def test_a_skill_with_scripts_needs_a_shell(self, tmp_path: Path) -> None:
        dest = tmp_path / "skills"
        lock = Lockfile(tmp_path / "l")
        folder = _skill_file(tmp_path / "src" / "scripted", "d", "backend")
        (folder / "run.sh").write_text("echo hi", encoding="utf-8")
        result = pin_local(folder, license_spdx="MIT", dest=dest)
        lock.add(result.entry)
        assert records_from_lock(lock, dest)[0].tools == ("shell",)
