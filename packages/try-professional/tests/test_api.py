"""The stable API. Other agents build on it, so what it promises is tested here."""

from __future__ import annotations

from pathlib import Path

import pytest

import try_professional
from try_professional import Company, CompanyError


@pytest.fixture
def company(tmp_path: Path) -> Company:
    return Company.create(tmp_path / "acme", name="Acme")


def test_the_package_exports_the_stable_api() -> None:
    assert try_professional.Company is Company
    assert set(try_professional.__all__) >= {"Company", "CompanyError"}


def test_create_builds_a_valid_default_company(company: Company) -> None:
    assert company.validate() == []
    refs = {role.ref for role in company.roles()}
    assert {"executive/ceo", "marketing/content-writer", "engineering/qa"} <= refs
    assert any(role.is_head for role in company.roles())
    # Legal is optional, and off by default.
    assert not any(ref.startswith("legal/") for ref in refs)
    assert company.org.gates == {}


def test_legal_comes_with_its_gate_when_asked_for(tmp_path: Path) -> None:
    from try_professional.packs import default_packs

    company = Company.create(tmp_path / "clinic", name="Clinic", packs=[*default_packs(), "legal"])
    assert company.validate() == []
    assert "legal/counsel" in {role.ref for role in company.roles()}
    assert company.org.gates["legal"].reviewer == "legal/counsel"


def test_create_refuses_a_folder_that_has_a_company(company: Company) -> None:
    with pytest.raises(CompanyError):
        Company.create(company.root)


def test_open_needs_a_company(tmp_path: Path) -> None:
    with pytest.raises(CompanyError):
        Company.open(tmp_path / "nothing")


def test_a_custom_role_reports_to_its_head(company: Company) -> None:
    role = company.add_role(
        "marketing",
        "newsletter-editor",
        title="Newsletter Editor",
        mission="Write the weekly newsletter.",
        skill_needs=["write email newsletters"],
    )
    assert role.ref == "marketing/newsletter-editor"
    loaded = company.org.role(role.ref)
    assert loaded.reviewer == "marketing/marketing-lead"
    assert loaded.escalation == "marketing/marketing-lead"
    assert company.validate() == []


def test_an_invalid_role_changes_nothing(company: Company) -> None:
    before = (company.root / "org" / "org.yaml").read_text(encoding="utf-8")
    with pytest.raises(CompanyError):
        company.add_role("marketing", "bad", title="Bad", mission="x", reviewer="nobody/here")
    assert (company.root / "org" / "org.yaml").read_text(encoding="utf-8") == before
    assert not (company.root / "org" / "roles" / "marketing" / "bad.yaml").exists()
    with pytest.raises(CompanyError):
        company.add_role("no-such-department", "x", title="X", mission="x")


def test_a_dry_run_finishes_and_leaves_readable_results(company: Company) -> None:
    result = company.run("Write a two-line launch note", dry_run=True)
    assert result.status == "done"
    status = company.status(result.run_id)
    assert status.finished and status.phase == "done" and not status.questions
    assert company.deliverables(result.run_id) is not None
    assert "summary_in_user_language" in (company.final_report(result.run_id) or {})


def test_claude_code_files_include_every_role(company: Company) -> None:
    written = company.setup_claude_code()
    assert company.root / ".mcp.json" in written
    agents = list((company.root / ".claude" / "agents").glob("*.md"))
    assert len(agents) == len(company.roles())


def test_runs_lists_newest_first(company: Company) -> None:
    first = company.run("Write a two-line launch note", dry_run=True).run_id
    second = company.run("Write a one-line tagline", dry_run=True).run_id
    assert company.runs()[:2] == [second, first]


def test_the_claude_code_example_builds(tmp_path: Path) -> None:
    """examples/claude-code-agent is how docs/building-on.md starts, so it has to keep working."""
    import importlib.util

    script = Path(__file__).resolve().parents[3] / "examples" / "claude-code-agent" / "build_company.py"
    spec = importlib.util.spec_from_file_location("build_company", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    built = module.build(tmp_path / "acme")
    assert "marketing/newsletter-editor" in {role.ref for role in built.roles()}
    assert (built.root / ".claude" / "agents").is_dir()
