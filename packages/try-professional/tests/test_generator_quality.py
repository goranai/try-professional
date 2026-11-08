"""The problems the org generator evaluation found on real runs: heads, trades, names, language, and gaps."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from try_professional.cli import main
from try_professional.generator import activate, create_proposal, load_proposal, render
from try_professional.generator.architect import anchor_departments, check_proposal, run_architect
from try_professional.generator.compiler import ProposalError, compile_org, seniority
from try_professional.generator.proposal import OrgProposal, asked_for_language, normalize_language, settle_language
from try_professional.generator.provision import ProvisionResult, needs_without_a_skill
from try_professional.generator.templates import load_template
from try_professional.org import load_bundle
from try_professional.packs import available_packs, pack_department, pack_role
from try_professional.skills import Decisions, RerankCache, SkillCatalog, compute_loadouts, rerank, shortlist
from try_professional.skills.catalog import SkillRecord
from try_professional.skills.rerank import Candidate, judge_prompt, judge_system
from try_professional.skills.trade import off_trade_reason, ties_of

from .test_generator import Architect, Judge, _keep, _role, _template
from .test_memory import FakeEmbedder


def _department(*roles: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"id": "work", "name": "Work", "roles": list(roles), **extra}


def _compile(*departments: dict[str, Any]) -> Any:
    return check_proposal(_template(), {"company_name": "X", "departments": list(departments)})[1]


def _record(skill_id: str, description: str = "does useful work", **changes: Any) -> SkillRecord:
    values: dict[str, Any] = {"category": "marketing", "content_sha256": "a" * 64}
    return SkillRecord(skill_id=skill_id, name=skill_id, description=description, **{**values, **changes})


def _org(description: str = "", **business: Any) -> Any:
    compiled = compile_org(_template(), _template().keep_all("X"))
    if description:
        compiled.org["company"]["description"] = description
    if business:
        compiled.org["company"]["business"] = business
    return load_bundle(compiled.org, compiled.roles)


class TestTheCompilerSettlesTheHead:
    def test_a_lone_role_heads_its_department(self) -> None:
        compiled = _compile(_department(_role("writer")))
        department = next(d for d in compiled.org["departments"] if d["id"] == "work")
        assert department["head"] == "writer"
        assert "skill_propose" in compiled.roles["work/writer"]["allowed_tools"]
        assert any("named no head" in note for note in compiled.notes)

    def test_the_most_senior_title_is_promoted(self) -> None:
        compiled = _compile(_department(_role("writer", name="Copywriter"), _role("boss", name="Creative Director")))
        department = next(d for d in compiled.org["departments"] if d["id"] == "work")
        assert department["head"] == "boss"
        assert compiled.roles["work/writer"]["escalation"] == "work/boss"
        assert "skill_propose" not in compiled.roles["work/writer"]["allowed_tools"]

    def test_a_title_the_model_didnt_mark_gives_no_more_than_a_head_gets(self) -> None:
        compiled = _compile(_department(_role("boss", name="Head of Work")))
        assert not compiled.issues()
        assert set(compiled.roles["work/boss"]["allowed_tools"]) <= {
            "workspace_read",
            "workspace_list",
            "workspace_write",
            "memory_recall",
            "memory_write",
            "skill_search",
            "skill_get",
            "skill_propose",
        }

    def test_of_two_heads_the_more_senior_one_stays(self) -> None:
        compiled = _compile(
            _department(_role("lead", "head", name="Team Lead"), _role("boss", "head", name="Chief Editor"))
        )
        department = next(d for d in compiled.org["departments"] if d["id"] == "work")
        assert department["head"] == "boss"
        assert "skill_propose" not in compiled.roles["work/lead"]["allowed_tools"]
        assert "TaskPlan" not in compiled.roles["work/lead"]["outputs"]
        assert any("named 2 heads" in note for note in compiled.notes)

    def test_a_reviewer_isnt_promoted_over_someone_doing_the_work(self) -> None:
        compiled = _compile(
            _department(_role("checker", "reviewer", name="Senior Reviewer"), _role("writer", name="Writer"))
        )
        department = next(d for d in compiled.org["departments"] if d["id"] == "work")
        assert department["head"] == "writer"

    @pytest.mark.parametrize(
        "roles",
        [
            [_role("a", name="Writer"), _role("b", name="Designer")],
            [_role("a", name="Art Director"), _role("b", name="Copy Director")],
            [_role("a", "head", name="Writer"), _role("b", "head", name="Designer")],
        ],
    )
    def test_only_a_real_tie_goes_back_to_the_model(self, roles: list[dict[str, Any]]) -> None:
        with pytest.raises(ProposalError, match="exactly one head"):
            _compile(_department(*roles))

    def test_a_kept_template_role_can_be_promoted(self) -> None:
        compiled = _compile(
            {"id": "scriptwriting", "name": "S", "from_template": "scriptwriting", "keep_roles": ["scriptwriter"]}
        )
        assert not compiled.issues()
        role = compiled.roles["scriptwriting/scriptwriter"]
        assert "skill_propose" in role["allowed_tools"]
        assert role["outputs"][0] == "TaskPlan"
        assert role["escalation"] == "executive/ceo"

    def test_a_kept_head_can_be_replaced_by_a_more_senior_new_one(self) -> None:
        compiled = _compile(
            {
                "id": "scriptwriting",
                "name": "S",
                "from_template": "scriptwriting",
                "keep_roles": ["head-writer", "scriptwriter"],
                "roles": [_role("chief", "head", name="Chief Writer")],
            }
        )
        assert not compiled.issues()
        department = next(d for d in compiled.org["departments"] if d["id"] == "scriptwriting")
        assert department["head"] == "chief"
        assert "skill_propose" not in compiled.roles["scriptwriting/head-writer"]["allowed_tools"]

    def test_no_retry_is_paid_for(self) -> None:
        client = Architect({"company_name": "X", "departments": [_department(_role("writer"))]})
        result = run_architect("a shop", _template(), client, "m")
        assert result.calls == 1
        assert result.proposal.departments[0].roles[0].kind == "worker"

    @pytest.mark.parametrize(
        "title,rank",
        [
            ("Chief of Staff", 3),
            ("CTO", 3),
            ("Head of Sales", 2),
            ("Creative Director", 2),
            ("QA Lead", 1),
            ("Writer", 0),
        ],
    )
    def test_seniority_comes_from_the_title(self, title: str, rank: int) -> None:
        assert seniority(title) == rank


class TestOtherTrades:
    @pytest.mark.parametrize(
        "skill_id",
        ["nexscope-ai/eCommerce-Skills/shopify-marketing", "nexscope-ai/eCommerce-Skills/tiktok-shop-ads"],
    )
    def test_a_clinic_doesnt_get_store_skills(self, skill_id: str) -> None:
        reason = off_trade_reason(_record(skill_id), _org("A neighbourhood physiotherapy clinic"))
        assert reason is not None
        assert "isn't" in reason or "doesn't use" in reason

    def test_a_store_does(self) -> None:
        org = _org("An online store selling handmade candles")
        assert off_trade_reason(_record("a/shopify-marketing"), org) is None

    def test_a_named_platform_rules_out_the_others(self) -> None:
        org = _org("A YouTube Shorts content company")
        assert off_trade_reason(_record("a/linkedin/li-plan"), org) is not None
        assert off_trade_reason(_record("postzen-dev/instagram-agent-skill/skills/ig-plan"), org) is not None
        assert off_trade_reason(_record("a/youtube-seo"), org) is None

    def test_stated_channels_decide(self) -> None:
        org = _org("A neighbourhood bakery", channels=["Instagram", "email"])
        assert off_trade_reason(_record("a/instagram-plan"), org) is None
        assert off_trade_reason(_record("a/linkedin-plan"), org) is not None

    def test_with_no_channels_known_a_social_skill_stays_in(self) -> None:
        assert off_trade_reason(_record("a/instagram-plan"), _org("A neighbourhood bakery")) is None

    def test_a_skill_naming_several_platforms_is_general(self) -> None:
        record = _record("a/hook-writing", "Write hooks for TikTok, Instagram Reels, and YouTube Shorts.")
        assert ties_of(record) == []
        assert off_trade_reason(record, _org("A physiotherapy clinic", channels=["local search"])) is None

    def test_a_description_naming_one_platform_counts(self) -> None:
        record = _record("a/store-copy", "Write product pages for your Shopify store.")
        assert off_trade_reason(record, _org("A physiotherapy clinic")) is not None

    def test_words_inside_other_words_dont_count(self) -> None:
        assert ties_of(_record("a/passover-planner", "Plan an event for a client.")) == []
        assert off_trade_reason(_record("a/shopify-x"), _org("A woodworking workshop")) is not None

    def test_tiktok_shop_isnt_also_tiktok(self) -> None:
        assert [tie.label for tie in ties_of(_record("a/tiktok-shop-ads"))] == ["TikTok Shop"]

    def test_no_context_filters_nothing(self) -> None:
        assert off_trade_reason(_record("a/shopify-marketing"), _org()) is None

    def test_a_platform_the_company_uses_is_in_context_whatever_the_industry(self) -> None:
        org = _org("A physiotherapy clinic", channels=["Shopify"])
        assert off_trade_reason(_record("a/shopify-marketing"), org) is None

    def _catalog(self, tmp_path: Path) -> Any:
        catalog = SkillCatalog(tmp_path / "c.sqlite", FakeEmbedder())
        catalog.rebuild([_record("a/shopify-hooks", "write a video hook"), _record("a/hooks", "write a video hook")])
        return catalog

    def test_shortlists_leave_them_out(self, tmp_path: Path) -> None:
        catalog = self._catalog(tmp_path)
        org = _org("A physiotherapy clinic")
        lists = shortlist(org, catalog.eligible(), catalog.embedder, Decisions(tmp_path / "d.yaml"), floor=0.0)
        catalog.close()
        found = {c.record.skill_id for items in lists.values() for c in items}
        assert "a/hooks" in found
        assert "a/shopify-hooks" not in found

    def test_a_persons_approval_overrides_it(self, tmp_path: Path) -> None:
        catalog = self._catalog(tmp_path)
        org = _org("A physiotherapy clinic")
        decisions = Decisions(tmp_path / "d.yaml")
        role = "scriptwriting/scriptwriter"
        decisions.decide(role, "a/shopify-hooks", "approved", by="a person", content_sha256="a" * 64)
        usage = lambda r, s: {"uses": 0, "passed": 0, "failed": 0, "idle_runs": 0}  # noqa: E731
        loadouts = compute_loadouts(org, catalog.eligible(), catalog.embedder, decisions, usage)
        catalog.close()
        assert "a/shopify-hooks" in {m.record.skill_id for m in loadouts[role].active}
        assert "other trade" not in str(loadouts[role].excluded.get("a/shopify-hooks", ""))
        assert "doesn't use" in loadouts["scriptwriting/head-writer"].excluded["a/shopify-hooks"]

    def test_an_off_trade_skill_doesnt_fill_a_need(self, tmp_path: Path) -> None:
        catalog = SkillCatalog(tmp_path / "c.sqlite", FakeEmbedder())
        org = _org("A physiotherapy clinic")
        need = next(iter(org.role("scriptwriting/scriptwriter").skill_needs))
        catalog.rebuild([_record("a/shopify-x", need)])
        assert need in needs_without_a_skill(org, catalog, catalog.embedder)
        catalog.close()

    def test_the_judge_reads_the_business_as_data(self) -> None:
        role = _org().role("scriptwriting/scriptwriter")
        prompt = judge_prompt(role, [Candidate(_record("a/x"), 0.5, "need")], "A physiotherapy clinic")
        assert "<untrusted" in prompt.split("A physiotherapy clinic")[0]
        assert "## This business" in prompt

    def test_the_judge_only_rules_on_trade_when_it_knows_the_business(self) -> None:
        assert "this business isn't in" in judge_system("A physiotherapy clinic")
        assert "this business isn't in" not in judge_system("")

    def test_a_different_business_isnt_served_an_old_judgment(self, tmp_path: Path) -> None:
        role = _org().role("scriptwriting/scriptwriter")
        record = _record("a/x")
        assert RerankCache.key(role, record, "m", "a clinic") != RerankCache.key(role, record, "m", "a shop")

    def test_the_judge_sees_the_companys_other_roles(self) -> None:
        """Told to leave a skill to "a neighbouring role", it guessed who that was. Now it's shown them."""
        from try_professional.skills.rerank import colleagues

        org = _org()
        role = org.role("scriptwriting/scriptwriter")
        others = colleagues(org, role)
        assert role.ref not in others
        assert all(ref in others for ref in org.roles if ref != role.ref)
        prompt = judge_prompt(role, [Candidate(_record("a/x"), 0.5, "need")], "", others)
        assert "## The company's other roles" in prompt

    def test_other_colleagues_arent_served_an_old_judgment(self) -> None:
        role = _org().role("scriptwriting/scriptwriter")
        record = _record("a/x")
        assert RerankCache.key(role, record, "m", "", "- a/writer") != RerankCache.key(role, record, "m", "", "")

    def test_rerank_passes_the_business_to_the_judge(self, tmp_path: Path) -> None:
        catalog = self._catalog(tmp_path)
        org = _org("A physiotherapy clinic")
        lists = shortlist(org, catalog.eligible(), catalog.embedder, Decisions(tmp_path / "d.yaml"), floor=0.0)
        lists = {ref: items for ref, items in lists.items() if ref == "scriptwriting/scriptwriter"}
        judge = Judge()
        cache = RerankCache(tmp_path / "r.sqlite")
        rerank(org, lists, judge, "m", cache)
        cache.close()
        catalog.close()
        assert "A physiotherapy clinic" in str(judge.requests[0].messages[0])


class TestNames:
    def test_every_pack_role_has_a_proper_title(self) -> None:
        for pack in available_packs():
            for role_id in pack_department(pack)["roles"]:
                title = pack_role(pack, role_id).get("title")
                assert title, f"{pack}/{role_id}"
                assert title != role_id
                assert "-" not in title

    def test_a_role_with_no_title_never_shows_a_raw_id(self) -> None:
        org = _org()
        role = org.role("scriptwriting/scriptwriter")
        from dataclasses import replace

        assert replace(role, id="compliance-reviewer", title="").display_name == "Compliance Reviewer"
        assert replace(role, id="seo-lead", title="").display_name == "SEO Lead"

    def test_compliance_reviewer_reads_as_a_name(self) -> None:
        assert _org().role("legal/compliance-reviewer").display_name == "Compliance Reviewer"

    def test_escaped_names_are_cleaned(self) -> None:
        proposal = OrgProposal.model_validate(
            {
                "company_name": "X",
                "departments": [_department(_role("a", "head", name="Press &amp; Content"), name="R &amp; D")],
            }
        )
        assert proposal.departments[0].name == "R & D"
        assert proposal.departments[0].roles[0].name == "Press & Content"


class TestLanguage:
    @pytest.mark.parametrize(
        "given,code",
        [
            ("id-ID", "id"),
            ("id_id", "id"),
            ("ID", "id"),
            ("en-US", "en"),
            ("en", "en"),
            ("pt-BR", "pt-BR"),
            ("zh-hant", "zh-Hant"),
            ("zh-tw", "zh-TW"),
            ("auto", "auto"),
            (None, "auto"),
        ],
    )
    def test_one_code_for_one_language(self, given: Any, code: str) -> None:
        assert normalize_language(given) == code

    def test_a_bad_code_is_refused(self) -> None:
        with pytest.raises(ValueError):
            OrgProposal.model_validate(
                {"company_name": "X", "working_language": "Klingon!", "departments": [_department(_role())]}
            )

    @pytest.mark.parametrize(
        "text,code",
        [
            ("A store with all content in Indonesian", "id"),
            ("An Indonesian-language education channel", "id"),
            ("Perusahaan dengan konten berbahasa Indonesia, dalam bahasa Indonesia", "id"),
            ("A newsletter written in Spanish only", "es"),
            ("Una tienda con todo en español", "es"),
            ("Ein Laden, alles auf Deutsch", "de"),
            ("一家全部使用中文的商店", "zh"),
            ("A German-speaking law firm", "de"),
        ],
    )
    def test_an_explicit_request_is_recognized(self, text: str, code: str) -> None:
        assert asked_for_language(text) == code

    @pytest.mark.parametrize(
        "text",
        [
            "UMKM Indonesia yang menjual keripik pisang secara online",
            "A Spanish restaurant",
            "A catering business that delivers office lunches",
            "A Chinese medicine clinic",
            "A shop in Germany",
            "Una tienda para los artesanos",
            "一家在线商店",
        ],
    )
    def test_a_place_a_trade_or_the_descriptions_own_language_asks_for_nothing(self, text: str) -> None:
        assert asked_for_language(text) is None

    @pytest.mark.parametrize(
        "description",
        ["A catering business", "UMKM Indonesia yang menjual keripik pisang secara online", "一家在线商店"],
    )
    def test_any_company_follows_each_command_by_default(self, description: str) -> None:
        result = run_architect(description, _template(), Architect(_keep()), "m")
        assert result.compiled.org["pipeline"]["working_language"] == "auto"

    def test_a_model_cant_lock_a_company_to_a_language_nobody_asked_for(self) -> None:
        answer = {**_keep(), "working_language": "en"}
        result = run_architect("A catering business", _template(), Architect(answer), "m")
        assert result.compiled.org["pipeline"]["working_language"] == "auto"

    def test_an_asked_for_language_is_fixed_in_one_form(self) -> None:
        answer = {**_keep(), "working_language": "id-ID"}
        result = run_architect("A store with all content in Indonesian", _template(), Architect(answer), "m")
        assert result.compiled.org["pipeline"]["working_language"] == "id"

    def test_the_request_counts_even_if_the_model_missed_it(self) -> None:
        result = run_architect("All content in Indonesian, please", _template(), Architect(_keep()), "m")
        assert result.compiled.org["pipeline"]["working_language"] == "id"

    def test_a_language_the_names_dont_cover_is_left_to_the_model(self) -> None:
        assert settle_language("sw", "A Swahili radio station") == "sw"

    def test_asking_for_it_while_regenerating_counts(self) -> None:
        result = run_architect("A store", _template(), Architect(_keep()), "m", feedback="Make all content in French")
        assert result.compiled.org["pipeline"]["working_language"] == "fr"

    def test_with_no_model_an_explicit_request_still_counts(self, tmp_path: Path) -> None:
        bundle = create_proposal(
            tmp_path, FakeEmbedder(), description="A store with all content in Indonesian", discovery=False
        )
        assert bundle.compiled.org["pipeline"]["working_language"] == "id"

    def test_a_plain_template_follows_each_command(self, tmp_path: Path) -> None:
        bundle = create_proposal(tmp_path, FakeEmbedder(), template_id="marketing-agency", discovery=False)
        assert bundle.compiled.org["pipeline"]["working_language"] == "auto"


class TestDepartmentNames:
    def _proposal(self, *departments: dict[str, Any]) -> OrgProposal:
        return OrgProposal.model_validate({"company_name": "X", "departments": list(departments)})

    def _names(self, proposal: OrgProposal, template: str) -> list[tuple[str, str]]:
        anchored, _ = anchor_departments(proposal, load_template(template))
        return [(d.id, d.name) for d in anchored.departments]

    def test_a_longer_id_lands_on_the_template_name(self) -> None:
        proposal = self._proposal(_department(_role("a", "head"), id="client-strategy", name="Client Strategy"))
        assert self._names(proposal, "marketing-agency") == [("strategy", "Strategy")]

    def test_an_alias_lands_on_the_template_name(self) -> None:
        proposal = self._proposal(_department(_role("a", "head"), id="account-management", name="Account Management"))
        assert self._names(proposal, "marketing-agency") == [("client-services", "Client Services")]

    def test_a_template_department_keeps_its_name(self) -> None:
        proposal = self._proposal(
            {"id": "trend-research", "name": "Audience Research", "from_template": "trend-research"}
        )
        assert self._names(proposal, "youtube-shorts-studio") == [("trend-research", "Trend Research")]

    def test_a_different_department_isnt_forced_into_its_starting_point(self) -> None:
        """Seen for real: Merchandising, started from the agency template's Strategy. Those aren't the same."""
        proposal = self._proposal(
            {
                "id": "merchandising",
                "name": "Merchandising",
                "from_template": "strategy",
                "keep_roles": ["strategy-lead"],
            }
        )
        assert self._names(proposal, "marketing-agency") == [("merchandising", "Merchandising")]

    def test_the_closest_template_decides_before_the_others(self) -> None:
        proposal = self._proposal(_department(_role("a", "head"), id="channel-analytics", name="Channel Analytics"))
        assert self._names(proposal, "youtube-shorts-studio") == [("analytics", "Analytics")]

    def test_a_partial_match_only_counts_in_the_closest_template(self) -> None:
        """Seen in replay: Product & Editorial Content became Product, from the software template."""
        proposal = self._proposal(_department(_role("a", "head"), id="product-content", name="Product Content"))
        assert self._names(proposal, "marketing-agency") == [("product-content", "Product Content")]

    def test_a_trade_of_its_own_keeps_its_name(self) -> None:
        proposal = self._proposal(_department(_role("a", "head"), id="clinical", name="Clinical Services"))
        assert self._names(proposal, "general-company") == [("clinical", "Clinical Services")]

    def test_two_departments_cant_land_on_one_name(self) -> None:
        proposal = self._proposal(
            _department(_role("a", "head"), id="client-strategy", name="Client Strategy"),
            _department(_role("b", "head"), id="strategy", name="Strategy"),
        )
        assert sorted(self._names(proposal, "marketing-agency")) == [
            ("client-strategy", "Client Strategy"),
            ("strategy", "Strategy"),
        ]

    def test_the_same_answer_compiles_to_the_same_company(self) -> None:
        answer = {"company_name": "X", "departments": [_department(_role("a", "head"), id="client-strategy", name="C")]}
        template = load_template("marketing-agency")
        first = run_architect("an agency", template, Architect(answer), "m")
        second = run_architect("an agency", template, Architect(answer), "m")
        assert first.compiled.org == second.compiled.org
        assert any("as templates call it" in note for note in first.notes)

    def test_a_change_a_person_asked_for_isnt_undone(self) -> None:
        answer = {"company_name": "X", "departments": [_department(_role("a", "head"), id="client-strategy", name="C")]}
        template = load_template("marketing-agency")
        result = run_architect("an agency", template, Architect(answer), "m", feedback="Call it Client Strategy")
        assert [d["id"] for d in result.compiled.org["departments"]][1] == "client-strategy"

    def test_the_architect_asks_for_repeatable_answers(self) -> None:
        client = Architect(_keep())
        run_architect("a shop", _template(), client, "m")
        assert client.requests[0].temperature == 0.0


class TestBusinessContext:
    def test_the_business_reaches_the_company_file(self, tmp_path: Path) -> None:
        answer = {**_keep(), "business": {"industry": "physiotherapy", "channels": ["Instagram"]}}
        bundle = create_proposal(
            tmp_path, FakeEmbedder(), description="A clinic", client=Architect(answer), model="m", discovery=False
        )
        company = bundle.compiled.org["company"]
        assert company["description"] == "A clinic"
        assert company["business"]["industry"] == "physiotherapy"
        assert bundle.org.business.channels == ("Instagram",)
        assert "physiotherapy" in bundle.org.business.text
        assert load_proposal(tmp_path, bundle.id).org.business.text == bundle.org.business.text

    def test_a_company_with_a_business_passes_its_own_schema(self, tmp_path: Path) -> None:
        answer = {**_keep(), "business": {"industry": "a", "products": ["b"], "channels": ["c"]}}
        bundle = create_proposal(
            tmp_path, FakeEmbedder(), description="A clinic", client=Architect(answer), model="m", discovery=False
        )
        assert not bundle.compiled.issues()


class TestGapsStayVisible:
    def _bundle(self, tmp_path: Path, gaps: dict[str, list[str]]) -> Any:
        bundle = create_proposal(tmp_path, FakeEmbedder(), template_id="marketing-agency", discovery=False)
        bundle.provisioned = ProvisionResult(gaps=gaps)
        bundle.save()
        return bundle

    def test_thin_coverage_is_said_plainly(self, tmp_path: Path) -> None:
        bundle = create_proposal(tmp_path, FakeEmbedder(), template_id="marketing-agency", discovery=False)
        gaps = {ref: list(role.skill_needs) for ref, role in bundle.org.roles.items()}
        bundle.provisioned = ProvisionResult(gaps=gaps)
        screen = render(bundle)
        assert "Skill coverage: 0 of" in screen
        assert "That's thin." in screen
        assert "GAP: no skill found for" in screen
        assert "trypro skills gaps" in screen

    def test_good_coverage_isnt_called_thin(self, tmp_path: Path) -> None:
        bundle = create_proposal(tmp_path, FakeEmbedder(), template_id="marketing-agency", discovery=False)
        ref, role = next(iter(bundle.org.roles.items()))
        bundle.provisioned = ProvisionResult(gaps={ref: [role.skill_needs[0]]})
        screen = render(bundle)
        assert "Skill coverage:" in screen
        assert "That's thin." not in screen

    def test_skills_left_out_for_their_trade_are_listed(self, tmp_path: Path) -> None:
        bundle = create_proposal(tmp_path, FakeEmbedder(), template_id="marketing-agency", discovery=False)
        bundle.provisioned = ProvisionResult(off_trade=[{"skill_id": "a/shopify", "reason": "tied to Shopify"}])
        assert "OTHER TRADE  a/shopify: tied to Shopify" in render(bundle)

    def test_approving_saves_them_as_skill_gaps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bundle = create_proposal(tmp_path, FakeEmbedder(), template_id="marketing-agency", discovery=False)
        ref, role = next(iter(bundle.org.roles.items()))
        bundle.provisioned = ProvisionResult(gaps={ref: [role.skill_needs[0]]})
        (tmp_path / "trypro.yaml").write_text("", encoding="utf-8")
        activate(bundle, tmp_path, FakeEmbedder(), by="a person")
        monkeypatch.chdir(tmp_path)
        assert main(["skills", "gaps"]) == 0
        output = capsys.readouterr().out
        assert ref in output
        assert role.skill_needs[0] in output
        assert yaml.safe_load((tmp_path / "org" / "org.yaml").read_text(encoding="utf-8"))
