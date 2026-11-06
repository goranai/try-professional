"""The trypro command, the way a user runs it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from try_professional.cli import main


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--company", "Demo", "--skip-model"]) == 0
    return tmp_path


def _run_id(output: str) -> str:
    match = re.search(r"run-[0-9a-f]+", output)
    assert match, output
    return match.group()


def test_init_creates_a_working_company(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--company", "Demo", "--skip-model"]) == 0
    assert "9 departments and 22 roles" in capsys.readouterr().out
    assert (tmp_path / "org" / "org.yaml").is_file()
    assert (tmp_path / "org" / "roles" / "engineering" / "backend.yaml").is_file()
    assert (tmp_path / "trypro.yaml").is_file()
    assert main(["org", "validate"]) == 0


def test_init_refuses_to_overwrite(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / "org" / "org.yaml").write_text("mine", encoding="utf-8")
    assert main(["init"]) == 1
    assert (project / "org" / "org.yaml").read_text(encoding="utf-8") == "mine"
    assert main(["init", "--force", "--skip-model"]) == 0


def test_init_with_selected_packs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--packs", "engineering,legal", "--skip-model"]) == 0
    org = yaml.safe_load((tmp_path / "org" / "org.yaml").read_text(encoding="utf-8"))
    assert [d["id"] for d in org["departments"]] == ["executive", "engineering", "legal"]


def test_init_rejects_unknown_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--packs", "astrology"]) == 2


def test_config_never_holds_a_key(project: Path) -> None:
    text = (project / "trypro.yaml").read_text(encoding="utf-8")
    assert "sk-" not in text
    assert yaml.safe_load(text)["model"]["api_key_env"] == "TRYPRO_LLM_API_KEY"


def test_runtime_data_is_ignored_by_git(project: Path) -> None:
    assert (project / ".trypro" / ".gitignore").read_text(encoding="utf-8").strip() == "*"


def test_dry_run_works_with_no_key(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TRYPRO_LLM_API_KEY", raising=False)
    capsys.readouterr()
    assert main(["run", "--dry-run", "Build a health check endpoint"]) == 0
    output = capsys.readouterr().out
    assert ": done" in output
    assert "scripted" in output.lower(), "a dry run must say it isn't real work"

    run_id = _run_id(output)
    assert main(["audit", run_id]) == 0
    assert "Audit log OK" in capsys.readouterr().out
    assert main(["status", run_id]) == 0
    assert "engineering/backend" in capsys.readouterr().out
    assert main(["status"]) == 0


def test_tampered_audit_log_fails_the_command(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    capsys.readouterr()
    main(["run", "--dry-run", "Goal"])
    run_id = _run_id(capsys.readouterr().out)
    path = project / ".trypro" / "runs" / run_id / "audit.jsonl"
    path.write_text(path.read_text(encoding="utf-8").replace("Goal", "Gaol"), encoding="utf-8")
    assert main(["audit", run_id]) == 1
    assert "FAILED" in capsys.readouterr().out


def test_goal_in_any_language(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for goal in ("Buatkan endpoint", "构建健康检查", "أنشئ نقطة نهاية", "Crear un endpoint"):
        assert main(["run", "--dry-run", goal]) == 0


def test_pack_commands(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["pack", "remove", "sales"]) == 0
    assert not (project / "org" / "roles" / "sales").exists()
    capsys.readouterr()
    assert main(["pack", "list"]) == 0
    assert "  sales" in capsys.readouterr().out
    assert main(["pack", "add", "sales"]) == 0
    assert main(["pack", "add", "sales"]) == 1
    assert main(["pack", "remove", "executive"]) == 1
    assert main(["pack", "add", "astrology"]) == 1
    assert main(["org", "validate"]) == 0


def test_org_show(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    capsys.readouterr()
    assert main(["org", "show"]) == 0
    output = capsys.readouterr().out
    assert "ceo (head)" in output and "executive [always]" in output


def test_validate_reports_a_broken_org(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / "org" / "roles" / "engineering" / "qa.yaml").unlink()
    capsys.readouterr()
    assert main(["org", "validate"]) == 1
    assert "ORG004" in capsys.readouterr().out


def test_blocked_run_flow_with_decide_and_resume(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = project / "org" / "org.yaml"
    org = yaml.safe_load(path.read_text(encoding="utf-8"))
    org["pipeline"]["budget"]["max_tokens_per_run"] = 1
    path.write_text(yaml.safe_dump(org), encoding="utf-8")

    capsys.readouterr()
    assert main(["run", "--dry-run", "Goal"]) == 3
    output = capsys.readouterr().out
    assert "Needs a decision" in output
    run_id = _run_id(output)
    request_id = re.search(r"hum-\d+", output).group()  # type: ignore[union-attr]

    assert main(["decide", run_id, request_id, "nonsense"]) == 1
    assert main(["decide", run_id, request_id, "resume", "--token-budget", "100000000"]) == 0
    assert main(["resume", run_id, "--dry-run"]) == 0


def test_real_run_without_the_key_or_config_fails_clearly(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = project / "trypro.yaml"
    config.write_text(
        "model:\n  provider: openai_compatible\n  base_url: https://x\n  api_key_env: UNSET_KEY_VAR\n"
        "  tiers: {standard: m}\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("UNSET_KEY_VAR", raising=False)
    capsys.readouterr()
    assert main(["run", "Goal"]) == 2
    assert "UNSET_KEY_VAR" in capsys.readouterr().out
    assert main(["doctor"]) == 1
    assert not list((project / ".trypro").glob("runs/*")), "no run should exist"


def test_outside_a_project_it_says_what_to_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["status"]) == 2
    assert "trypro init" in capsys.readouterr().out


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--version"])
    assert caught.value.code == 0
    assert "trypro" in capsys.readouterr().out
