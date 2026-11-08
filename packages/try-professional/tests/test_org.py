from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from try_professional.org import Org, OrgValidationError, load_org
from try_professional.org.loader import read_documents, validate_bundle
from try_professional.packs import add_pack, available_packs, install_org, remove_pack

DEFAULT_DEPARTMENTS = {
    "executive",
    "product",
    "engineering",
    "marketing",
    "sales",
    "legal",
    "finance",
    "operations",
    "research",
}


def test_default_org_matches_the_brief(org: Org) -> None:
    assert set(org.departments) == DEFAULT_DEPARTMENTS
    assert len(org.roles) == 22
    assert org.root_role == "executive/ceo"


def test_default_org_has_no_issues(tmp_path: Path) -> None:
    install_org(tmp_path, "Test", available_packs())
    documents = read_documents(tmp_path)
    assert validate_bundle(*documents) == []


def test_only_executive_is_always_active(org: Org) -> None:
    assert org.always_active() == ["executive"]


def test_reviewer_is_never_the_owner(org: Org) -> None:
    for ref in org.roles:
        reviewer = org.resolve_reviewer(ref)
        assert reviewer != ref
        assert org.has_role(reviewer)


def test_escalation_chain_of_non_heads_stays_in_department(org: Org) -> None:
    for ref, role in org.roles.items():
        if not org.is_head(ref) and role.escalation != "human":
            assert role.escalation.split("/")[0] == role.department


@pytest.mark.parametrize("pack", sorted(DEFAULT_DEPARTMENTS - {"executive"}))
def test_each_pack_can_be_removed_and_added_back(tmp_path: Path, pack: str) -> None:
    install_org(tmp_path, "Test", available_packs())
    remove_pack(tmp_path, pack)
    org = load_org(tmp_path)
    assert pack not in org.departments
    assert not (tmp_path / "roles" / pack).exists()
    add_pack(tmp_path, pack)
    assert pack in load_org(tmp_path).departments


def test_root_pack_cannot_be_removed(tmp_path: Path) -> None:
    install_org(tmp_path, "Test", available_packs())
    with pytest.raises(ValueError, match="root role"):
        remove_pack(tmp_path, "executive")


def test_removing_legal_removes_its_gate(tmp_path: Path) -> None:
    install_org(tmp_path, "Test", available_packs())
    remove_pack(tmp_path, "legal")
    org, roles = read_documents(tmp_path)
    assert validate_bundle(org, roles) == []
    assert "legal" not in (org.get("gates") or {})


def test_adding_legal_adds_its_gate(tmp_path: Path) -> None:
    install_org(tmp_path, "Test", ["executive", "marketing"])
    assert not read_documents(tmp_path)[0].get("gates")
    add_pack(tmp_path, "legal")
    org, roles = read_documents(tmp_path)
    assert org["gates"]["legal"]["reviewer"] == "legal/counsel"
    assert validate_bundle(org, roles) == []


def test_extension_keys_survive_pack_edits(tmp_path: Path) -> None:
    install_org(tmp_path, "Test", available_packs())
    path = tmp_path / "org.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["x-acme-owner"] = {"team": "platform"}
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    remove_pack(tmp_path, "sales")
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["x-acme-owner"] == {"team": "platform"}


def test_a_broken_org_fails_with_its_code(make_org: Callable[..., Org]) -> None:
    def break_it(org_dir: Path) -> None:
        path = org_dir / "roles" / "engineering" / "qa.yaml"
        role = yaml.safe_load(path.read_text(encoding="utf-8"))
        role["reviewer"] = "engineering/qa"
        path.write_text(yaml.safe_dump(role), encoding="utf-8")

    with pytest.raises(OrgValidationError) as caught:
        make_org(mutate=break_it)
    assert "ORG011" in {i.code for i in caught.value.issues}


def test_every_role_output_is_a_known_artifact(org: Org) -> None:
    from try_professional.core.artifacts import PAYLOAD_TYPES

    for role in org.roles.values():
        for output in role.outputs:
            assert output in PAYLOAD_TYPES, f"{role.ref}: {output}"


def test_heads_can_produce_task_plans(org: Org) -> None:
    for department in org.departments.values():
        if department.id != "executive":
            assert "TaskPlan" in org.role(department.head_ref).outputs, department.id


def test_reviewers_can_produce_verdicts(org: Org) -> None:
    for ref in org.roles:
        reviewer = org.role(org.resolve_reviewer(ref))
        assert "ReviewVerdict" in reviewer.outputs, f"{ref} is reviewed by {reviewer.ref}"
