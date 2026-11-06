from __future__ import annotations

from pathlib import Path

import pytest
from conftest import APACHE, GPL, MIT, write_skill

from skill_customs import Policy, check_license


@pytest.mark.parametrize("name", ["MIT", "mit", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"])
def test_allowlist_defaults(tmp_path: Path, name: str) -> None:
    verdict = check_license(write_skill(tmp_path / "s", license_name=name))
    assert verdict.allowed and verdict.commercial_use == "allowed"


@pytest.mark.parametrize("name", ["GPL-3.0", "AGPL-3.0", "MPL-2.0", "Unlicense", "CC0-1.0"])
def test_off_the_list_is_rejected(tmp_path: Path, name: str) -> None:
    verdict = check_license(write_skill(tmp_path / "s", license_name=name))
    assert not verdict.allowed and verdict.spdx == name


def test_the_allowlist_is_configurable(tmp_path: Path) -> None:
    skill = write_skill(tmp_path / "s", license_name="MPL-2.0")
    assert check_license(skill, Policy(allowed_licenses=("MPL-2.0",))).allowed


def test_unknown_is_rejected(tmp_path: Path) -> None:
    verdict = check_license(write_skill(tmp_path / "s", license_name=None))
    assert not verdict.allowed and verdict.spdx is None and verdict.commercial_use == "unknown"


def test_an_unrecognized_declaration_is_rejected(tmp_path: Path) -> None:
    assert not check_license(write_skill(tmp_path / "s", license_name="Do What You Like v9")).allowed


@pytest.mark.parametrize(
    "declared",
    ["CC-BY-NC-4.0", "CC BY-NC 4.0", "PolyForm-Noncommercial-1.0.0", "Free for non-commercial use"],
)
def test_non_commercial_terms_are_flagged(tmp_path: Path, declared: str) -> None:
    verdict = check_license(write_skill(tmp_path / "s", license_name=declared))
    assert not verdict.allowed and verdict.commercial_use == "restricted"


def test_non_commercial_in_a_license_file_is_flagged(tmp_path: Path) -> None:
    skill = write_skill(tmp_path / "s", license_name=None, LICENSE="You may use this for non-commercial purposes only.")
    assert check_license(skill).commercial_use == "restricted"


def test_proprietary_is_flagged(tmp_path: Path) -> None:
    verdict = check_license(write_skill(tmp_path / "s", license_name="Proprietary. See LICENSE.txt"))
    assert verdict.spdx == "PROPRIETARY" and verdict.commercial_use == "restricted"


def test_all_rights_reserved_counts_as_proprietary(tmp_path: Path) -> None:
    skill = write_skill(tmp_path / "s", license_name=None, LICENSE="Copyright 2026 Someone. All rights reserved.")
    assert check_license(skill).spdx == "PROPRIETARY"


@pytest.mark.parametrize("text,spdx", [(MIT, "MIT"), (APACHE, "Apache-2.0"), (GPL, "GPL-3.0")])
def test_license_files_are_recognized_by_wording(tmp_path: Path, text: str, spdx: str) -> None:
    skill = write_skill(tmp_path / "s", license_name=None, LICENSE=text)
    assert check_license(skill).spdx == spdx


def test_see_license_file_defers_to_the_file(tmp_path: Path) -> None:
    skill = write_skill(tmp_path / "s", license_name="See LICENSE.txt", LICENSE_txt=MIT)
    (skill / "LICENSE.txt").write_text(MIT, encoding="utf-8")
    assert check_license(skill).spdx == "MIT"


def test_repository_license_is_a_fallback(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "LICENSE").write_text(MIT, encoding="utf-8")
    verdict = check_license(write_skill(repo / "s", license_name=None), repo_root=repo)
    assert verdict.allowed and verdict.evidence == "repo-license-file"


class TestDisagreement:
    """A skill can't talk its way out of a stricter license found anywhere else."""

    def test_mit_claim_over_a_gpl_repository_is_rejected(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "LICENSE").write_text(GPL, encoding="utf-8")
        verdict = check_license(write_skill(repo / "s", license_name="MIT"), repo_root=repo)
        assert not verdict.allowed and verdict.spdx == "GPL-3.0"

    def test_mit_claim_over_a_non_commercial_file_is_rejected(self, tmp_path: Path) -> None:
        skill = write_skill(tmp_path / "s", license_name="MIT", LICENSE="Non-commercial use only.")
        verdict = check_license(skill)
        assert not verdict.allowed and verdict.commercial_use == "restricted"

    def test_an_unrecognized_file_blocks_a_clean_declaration(self, tmp_path: Path) -> None:
        skill = write_skill(tmp_path / "s", license_name="MIT", LICENSE="Custom terms apply.")
        assert not check_license(skill).allowed

    def test_two_allowed_licenses_are_accepted_and_noted(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "LICENSE").write_text(APACHE, encoding="utf-8")
        verdict = check_license(write_skill(repo / "s", license_name="MIT"), repo_root=repo)
        assert verdict.allowed and verdict.spdx == "MIT" and "different" in verdict.reason


class TestExpressions:
    def test_or_picks_an_allowed_option(self, tmp_path: Path) -> None:
        assert check_license(write_skill(tmp_path / "s", license_name="GPL-3.0 OR MIT")).spdx == "MIT"

    def test_and_needs_every_part_allowed(self, tmp_path: Path) -> None:
        verdict = check_license(write_skill(tmp_path / "s", license_name="MIT AND GPL-3.0"))
        assert not verdict.allowed and verdict.spdx == "GPL-3.0"

    def test_an_unknown_part_makes_it_unknown(self, tmp_path: Path) -> None:
        assert not check_license(write_skill(tmp_path / "s", license_name="MIT AND Mystery-1.0")).allowed
