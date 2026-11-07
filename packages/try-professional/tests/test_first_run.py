"""What someone meets in their first ten minutes: a missing token, a stopped command, thin skills, and a bug to report."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from try_professional import __version__
from try_professional.adapters.api_runner.scripted import default_payload
from try_professional.adapters.mcp import McpService
from try_professional.cli import main
from try_professional.core.orchestrator import Orchestrator
from try_professional.doctor import FAIL, OK, WARN, report, run_checks, scrub
from try_professional.generator import create_proposal
from try_professional.generator.provision import ProvisionResult
from try_professional.generator.pull import begin_design
from try_professional.generator.review import SKILL_REQUEST_URL, render
from try_professional.org import Org

from .test_generator import FakeEmbedder

ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"
_MODEL_VARIABLES = ("TRYPRO_LLM_API_KEY", "TRYPRO_LLM_BASE_URL", "TRYPRO_LLM_MODEL")


@pytest.fixture
def folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    for name in _MODEL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("try_professional.cli.FastEmbedEmbedder", lambda *a, **k: FakeEmbedder())
    monkeypatch.setattr("try_professional.cli._person_at_terminal", lambda: False)
    return tmp_path


class TestGitHubToken:
    def test_doctor_explains_how_to_set_it(self, folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
        main(["doctor"])
        output = capsys.readouterr().out
        assert "[warn] GITHUB_TOKEN isn't set" in output
        assert "gh auth token" in output
        assert "no skills" in output

    def test_doctor_is_happy_once_it_is_set(
        self, folder: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "a-token-of-some-length")
        main(["doctor"])
        output = capsys.readouterr().out
        assert "[ok] GITHUB_TOKEN is set" in output
        assert "a-token-of-some-length" not in output

    def test_a_token_in_env_file_counts(self, folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
        (folder / ".env").write_text("GITHUB_TOKEN=from-the-env-file\n", encoding="utf-8")
        main(["doctor"])
        assert "[ok] GITHUB_TOKEN is set" in capsys.readouterr().out

    def test_init_says_so_before_it_does_anything(self, folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
        main(["init", "--template", "marketing-agency"])
        output = capsys.readouterr().out
        assert output.index("gh auth token") < output.index("Approving needs a person")

    def test_a_person_can_stop_and_set_it_first(
        self, folder: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("try_professional.cli._person_at_terminal", lambda: True)
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")
        assert main(["init", "--template", "marketing-agency"]) == 2
        assert "Nothing was changed" in capsys.readouterr().out
        assert not (folder / "trypro.yaml").exists()
        assert not (folder / ".trypro").exists()

    def test_asking_for_no_skills_needs_no_warning(self, folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
        main(["init", "--template", "marketing-agency", "--no-discover"])
        assert "gh auth token" not in capsys.readouterr().out

    def test_with_a_token_nothing_is_said(
        self, folder: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "a-token-of-some-length")
        monkeypatch.setattr(
            "try_professional.generator.provision.discover_for_needs", lambda *a, **k: [], raising=False
        )
        main(["init", "--template", "marketing-agency", "--no-discover"])
        assert "gh auth token" not in capsys.readouterr().out

    def test_designing_over_mcp_says_so_before_any_design_work(self, folder: Path) -> None:
        action = begin_design(folder, FakeEmbedder(), "A small bakery")
        assert "gh auth token" in action["tell_the_person_first"]
        assert next(iter(action)) == "tell_the_person_first", "it should be the first thing a client reads"

    def test_designing_over_mcp_finds_a_token_in_env_file(self, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """There's no trypro.yaml yet, so nothing else has loaded .env, and discovery would have skipped it too."""
        (folder / ".env").write_text("GITHUB_TOKEN=from-the-env-file\n", encoding="utf-8")
        assert "tell_the_person_first" not in begin_design(folder, FakeEmbedder(), "A small bakery")
        assert os.environ["GITHUB_TOKEN"] == "from-the-env-file"
        monkeypatch.delenv("GITHUB_TOKEN")

    def test_the_review_screen_explains_it_too(self, folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
        bundle = create_proposal(folder, FakeEmbedder(), template_id="marketing-agency")
        assert "gh auth token" in render(bundle)


class TestDoctorReport:
    def _company(self, folder: Path) -> None:
        assert main(["init", "--company", "Secret Bakery Co", "--skip-model"]) == 0

    def test_it_works_with_no_company(self, folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["doctor", "--report"]) == 0
        output = capsys.readouterr().out
        assert __version__ in output
        assert "Python:" in output
        assert "System:" in output
        assert "no company" in output

    def test_it_holds_nothing_personal(
        self, folder: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._company(folder)
        monkeypatch.setenv("GITHUB_TOKEN", "planted-github-token-value")
        monkeypatch.setenv("TRYPRO_LLM_API_KEY", "planted-model-key-value")
        monkeypatch.setenv("TRYPRO_LLM_BASE_URL", "https://private.example.internal/v1")
        monkeypatch.setenv("TRYPRO_LLM_MODEL", "my-private-deployment")
        capsys.readouterr()
        assert main(["doctor", "--report"]) == 0
        output = capsys.readouterr().out
        for private in (
            "planted-github",
            "planted-model",
            "private.example",
            "my-private-deployment",
            "Secret Bakery",
            str(folder),
            str(folder).replace("\\", "/"),
            str(Path.home()),
            "executive/ceo",
        ):
            assert private not in output, private
        assert "company: 9 departments, 22 roles" in output

    def test_a_private_check_still_shows_something_was_wrong(self, folder: Path, capsys: Any) -> None:
        self._company(folder)
        (folder / "org" / "org.yaml").write_text("not: [valid\n", encoding="utf-8")
        capsys.readouterr()
        assert main(["doctor", "--report"]) == 0
        output = capsys.readouterr().out
        assert "[!!]" in output
        assert "org.yaml" not in output and str(folder) not in output

    def test_doctor_doesnt_crash_on_a_broken_company(self, folder: Path, capsys: Any) -> None:
        self._company(folder)
        (folder / "org" / "org.yaml").write_text("not: [valid\n", encoding="utf-8")
        capsys.readouterr()
        assert main(["doctor"]) == 1
        assert "couldn't check the company" in capsys.readouterr().out

    def test_scrub_removes_paths_keys_and_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("try_professional.doctor._user_name", lambda: "alice")
        monkeypatch.setattr("try_professional.doctor.socket.gethostname", lambda: "alices-laptop")
        text = f"{Path.home()}/x {Path.cwd()}/y planted-secret-value user alice on alices-laptop"
        clean = scrub(text, {"MY_API_KEY": "planted-secret-value", "HOME_COLOR": "not secret"})
        for private in (str(Path.home()), str(Path.cwd()), "planted-secret", "alice"):
            assert private not in clean

    def test_scrub_leaves_ordinary_words_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("try_professional.doctor._user_name", lambda: "al")
        assert "Python 3.13 on Windows" in scrub("Python 3.13 on Windows", {"SHORT_KEY": "abc"})

    def test_the_plain_doctor_says_what_it_found(self, folder: Path) -> None:
        self._company(folder)
        statuses = {check.status for check in run_checks(folder)}
        assert OK in statuses and WARN in statuses and FAIL not in statuses

    def test_the_report_is_fenced_to_paste(self, folder: Path) -> None:
        text = report(run_checks(folder))
        assert text.startswith("```text") and text.endswith("```")


class TestAStoppedCommandSaysWhatToDo:
    def _stopped(self, make_org: Callable[..., Org], tmp_path: Path, coordinator: bool) -> tuple[str, str]:
        def mutate(org_dir: Path) -> None:
            path = org_dir / "org.yaml"
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            doc["pipeline"]["budget"]["max_tokens_per_run"] = 3000
            path.write_text(yaml.safe_dump(doc), encoding="utf-8")

        orchestrator = Orchestrator(make_org(mutate=mutate), tmp_path / "home")
        service = McpService(orchestrator)
        run_id = (current := service.run_start("Add a health check endpoint"))["run_id"]
        for _ in range(100):
            if current["status"] == "blocked":
                break
            action = current["actions"][0]
            state = orchestrator.state(run_id)
            payload = default_payload(state.invocations[action["invocation_id"]], state, orchestrator.org)
            current = getattr(service, action["submit_with"])(run_id, action["invocation_id"], payload)
        assert current["status"] == "blocked"
        asked = McpService(orchestrator, coordinator=coordinator).run_next_action(run_id)
        return run_id, asked["needs_a_person"][0]["tell_the_person"]

    def test_it_gives_the_lines_to_run(self, make_org: Callable[..., Org], tmp_path: Path) -> None:
        run_id, message = self._stopped(make_org, tmp_path, True)
        assert f"/company continue {run_id}" in message
        assert f"trypro decide {run_id} " in message
        assert " resume" in message and " abandon" in message
        assert "<option>" not in message

    def test_it_says_exactly_what_happened(self, make_org: Callable[..., Org], tmp_path: Path) -> None:
        _, message = self._stopped(make_org, tmp_path, True)
        assert "over its limit of 3000" in message
        assert "by the server's estimate" in message
        assert "its work so far is saved" in message
        assert "Resuming adds 1500 tokens" in message

    def test_a_client_without_company_isnt_told_to_use_it(self, make_org: Callable[..., Org], tmp_path: Path) -> None:
        _, message = self._stopped(make_org, tmp_path, False)
        assert "/company" not in message
        assert "run_next_action" in message

    def test_the_command_line_says_what_comes_next(
        self, folder: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert main(["init", "--company", "Demo", "--skip-model"]) == 0
        path = folder / "org" / "org.yaml"
        org = yaml.safe_load(path.read_text(encoding="utf-8"))
        org["pipeline"]["budget"]["max_tokens_per_run"] = 1
        path.write_text(yaml.safe_dump(org), encoding="utf-8")
        capsys.readouterr()
        assert main(["run", "--dry-run", "Goal"]) == 3
        output = capsys.readouterr().out
        assert "Then: trypro resume run-" in output
        run_id = re.search(r"run-[0-9a-f]+", output).group()  # type: ignore[union-attr]
        request_id = re.search(r"hum-[0-9]+", output).group()  # type: ignore[union-attr]
        monkeypatch.setattr("try_professional.cli._person_at_terminal", lambda: True)
        assert main(["decide", run_id, request_id, "resume"]) == 0
        assert f"/company continue {run_id}" in capsys.readouterr().out

    def test_abandoning_doesnt_offer_to_continue(
        self, folder: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert main(["init", "--company", "Demo", "--skip-model"]) == 0
        path = folder / "org" / "org.yaml"
        org = yaml.safe_load(path.read_text(encoding="utf-8"))
        org["pipeline"]["budget"]["max_tokens_per_run"] = 1
        path.write_text(yaml.safe_dump(org), encoding="utf-8")
        capsys.readouterr()
        assert main(["run", "--dry-run", "Goal"]) == 3
        output = capsys.readouterr().out
        run_id = re.search(r"run-[0-9a-f]+", output).group()  # type: ignore[union-attr]
        request_id = re.search(r"hum-[0-9]+", output).group()  # type: ignore[union-attr]
        monkeypatch.setattr("try_professional.cli._person_at_terminal", lambda: True)
        assert main(["decide", run_id, request_id, "abandon"]) == 0
        assert "/company continue" not in capsys.readouterr().out


class TestThinCoverage:
    def _screen(self, folder: Path, *, covered: bool, discovery: list[str] | None = None) -> str:
        bundle = create_proposal(folder, FakeEmbedder(), template_id="marketing-agency", discovery=False)
        if covered:
            ref, role = next(iter(bundle.org.roles.items()))
            gaps = {ref: [role.skill_needs[0]]}
        else:
            gaps = {ref: list(role.skill_needs) for ref, role in bundle.org.roles.items()}
        bundle.provisioned = ProvisionResult(gaps=gaps, discovery=discovery or [])
        return render(bundle)

    def test_it_links_to_the_request_form(self, folder: Path) -> None:
        assert SKILL_REQUEST_URL in self._screen(folder, covered=False)

    def test_good_coverage_has_no_link(self, folder: Path) -> None:
        assert SKILL_REQUEST_URL not in self._screen(folder, covered=True)

    def test_with_no_token_the_answer_is_the_token(self, folder: Path) -> None:
        """Nothing was searched, so asking for skills would be the wrong advice."""
        screen = self._screen(folder, covered=False, discovery=["Skipped skill discovery: GITHUB_TOKEN isn't set"])
        assert SKILL_REQUEST_URL not in screen

    def test_the_link_goes_to_a_form_that_exists(self) -> None:
        assert (TEMPLATES / SKILL_REQUEST_URL.split("template=")[1]).is_file()


class TestReportingAndContributing:
    @pytest.mark.parametrize("name", ["bug_report", "feature_request", "skill_request", "runtime_support"])
    def test_each_issue_form_is_valid(self, name: str) -> None:
        form = yaml.safe_load((TEMPLATES / f"{name}.yml").read_text(encoding="utf-8"))
        assert form["name"] and form["description"]
        assert any(item.get("validations", {}).get("required") for item in form["body"])
        for item in form["body"]:
            assert item["type"] in ("markdown", "textarea", "input", "dropdown", "checkboxes")

    def test_a_bug_report_asks_for_the_diagnostic(self) -> None:
        form = yaml.safe_load((TEMPLATES / "bug_report.yml").read_text(encoding="utf-8"))
        ids = {item.get("id"): item for item in form["body"]}
        assert "trypro doctor --report" in ids["doctor"]["attributes"]["label"]
        assert ids["doctor"]["validations"]["required"]

    def test_security_doesnt_go_in_public_issues(self) -> None:
        config = yaml.safe_load((TEMPLATES / "config.yml").read_text(encoding="utf-8"))
        assert config["blank_issues_enabled"] is False
        assert any("advisories" in link["url"] for link in config["contact_links"])

    def test_the_changelog_has_this_version(self) -> None:
        assert f"## {__version__}" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    def test_a_first_pull_request_has_a_path(self) -> None:
        text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        assert "Your first pull request" in text
        assert "good%20first%20issue" in text

    def test_releasing_is_written_down(self) -> None:
        text = (ROOT / "docs" / "releasing.md").read_text(encoding="utf-8")
        assert "publish.yml" in text
        assert "ask" in text.lower()
