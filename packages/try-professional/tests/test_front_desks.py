"""One setup opens a company from any supported harness, without losing its records or a person's notes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from itertools import permutations
from pathlib import Path
from typing import Any

import pytest
import yaml

from try_professional import Company
from try_professional.adapters.mcp.front_desk import refresh_front_desks, write_front_desks
from try_professional.cli import main

HARNESS_NAMES = ("claude-code", "antigravity", "codex")
RULE_PATHS = ("CLAUDE.md", ".agents/rules/trypro-front-desk.md", "AGENTS.md")
PERSONAL_SERVERS = (
    '[mcp_servers.trypro]\ncommand = "my-personal-server"\n',
    '[ mcp_servers.trypro ]\ncommand = "my-personal-server"\n',
    'mcp_servers = { trypro = { command = "my-personal-server" } }\n',
    '[ "mcp_servers" . "trypro" ]\ncommand = "my-personal-server"\n',
    "[ 'mcp_servers' . 'trypro' ]\ncommand = 'my-personal-server'\n",
    'mcp_servers.trypro.command = "my-personal-server"\n',
    '[mcp_servers]\ntrypro = { command = "my-personal-server" }\n',
    '[mcp_servers]\ntrypro.command = "my-personal-server"\n',
    '[mcp_servers."try\\u0070ro"]\ncommand = "my-personal-server"\n',
)


def _assert_desks(root: Path, company: Company) -> None:
    for relative in RULE_PATHS:
        rules = " ".join((root / relative).read_text(encoding="utf-8").split())
        assert company.org.company_name in rules
        for ref in company.org.roles:
            assert f"`{ref}`" in rules
        assert "never do a role's work yourself" in rules
        assert "quick one-off whose answer stays here in our chat" in rules
        assert "say you did it alone" in rules
        assert "Only I decide: never choose for me" in rules
        assert "company_rules" in rules and "outcome_record" in rules
        assert "deliverables/" in rules and "<untrusted>" in rules
    claude = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["trypro"]
    antigravity = json.loads((root / ".agents/mcp_config.json").read_text(encoding="utf-8"))["mcpServers"]["trypro"]
    codex = (root / ".codex/config.toml").read_text(encoding="utf-8")
    codex_args = json.loads(
        next(line.removeprefix("args = ") for line in codex.splitlines() if line.startswith("args = "))
    )
    for args, client in ((claude["args"], "claude-code"), (antigravity["args"], "antigravity"), (codex_args, "codex")):
        assert "--coordinator" in args and "--role" not in args
        assert args[args.index("--dir") + 1] == str(root.resolve()).replace("\\", "/")
        if client != "claude-code":
            assert args[args.index("--client") + 1] == client
    assert f"command = {json.dumps(claude['command'])}" in codex
    assert "[mcp_servers.trypro]" in codex and "type =" not in codex
    assert (root / ".claude/commands/company.md").is_file()
    assert (root / ".agents/workflows/company.md").is_file()
    assert {p.stem for p in (root / ".claude/agents").glob("trypro-*.md")} == {
        "trypro-" + ref.replace("/", "-") for ref in company.org.roles
    }


@pytest.mark.parametrize("harness", HARNESS_NAMES)
def test_agents_writes_all_three_front_desks_in_one_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, harness: str
) -> None:
    company = Company.create(tmp_path, name="Example")
    monkeypatch.chdir(tmp_path)
    assert main(["agents", harness]) == 0
    _assert_desks(tmp_path, company)


def test_init_writes_and_refreshes_every_front_desk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--company", "First", "--skip-model"]) == 0
    _assert_desks(tmp_path, Company.open(tmp_path))
    for relative in RULE_PATHS:
        with (tmp_path / relative).open("a", encoding="utf-8") as out:
            out.write("\nMy own note.\n")
    assert main(["init", "--company", "Second", "--force", "--skip-model"]) == 0
    _assert_desks(tmp_path, Company.open(tmp_path))
    for relative in RULE_PATHS:
        text = (tmp_path / relative).read_text(encoding="utf-8")
        assert "My own note." in text and "First" not in text


@pytest.mark.parametrize(("first", "second"), list(permutations(HARNESS_NAMES, 2)))
def test_switching_harness_preserves_company_state_and_personal_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first: str, second: str
) -> None:
    company = Company.create(tmp_path, name="Example")
    result = company.run("Build a health check endpoint", dry_run=True)
    assert result.status == "done"
    monkeypatch.chdir(tmp_path)
    assert main(["agents", first, *(["--sandboxed"] if first == "claude-code" else [])]) == 0
    notes = [
        *RULE_PATHS,
        ".claude/commands/company.md",
        ".agents/workflows/company.md",
        ".claude/agents/trypro-research-analyst.md",
    ]
    for relative in notes:
        path = tmp_path / relative
        path.write_text(path.read_text(encoding="utf-8") + "\nMy own note.\n", encoding="utf-8")
    config = tmp_path / ".codex/config.toml"
    personal = '# My own model\nmodel = "mine"\n\n[mcp_servers.personal]\ncommand = "personal"\n'
    config.write_text(personal + config.read_text(encoding="utf-8"), encoding="utf-8")
    for relative in (".mcp.json", ".agents/mcp_config.json"):
        path = tmp_path / relative
        data = json.loads(path.read_text(encoding="utf-8"))
        data["mcpServers"]["personal"] = {"command": "personal"}
        path.write_text(json.dumps(data), encoding="utf-8")
    agent = tmp_path / ".claude/agents/trypro-research-analyst.md"
    agent.write_text(agent.read_text(encoding="utf-8").replace("model: sonnet", "model: opus"), encoding="utf-8")
    tracked = [
        tmp_path / "trypro.yaml",
        *list((tmp_path / "org").rglob("*.yaml")),
        *[p for folder in (".trypro", "deliverables") for p in (tmp_path / folder).rglob("*") if p.is_file()],
    ]
    before = {p: p.read_bytes() for p in tracked}
    assert main(["agents", second]) == 0
    assert before == {p: p.read_bytes() for p in tracked}
    for relative in notes:
        assert (tmp_path / relative).read_text(encoding="utf-8").endswith("\nMy own note.\n")
    assert config.read_text(encoding="utf-8").startswith(personal)
    for relative in (".mcp.json", ".agents/mcp_config.json"):
        data = json.loads((tmp_path / relative).read_text(encoding="utf-8"))
        assert data["mcpServers"]["personal"] == {"command": "personal"}
    assert "model: opus" in agent.read_text(encoding="utf-8")
    assert ("Bash" not in agent.read_text(encoding="utf-8")) == (first == "claude-code")
    _assert_desks(tmp_path, Company.open(tmp_path))


def test_role_changes_refresh_every_desk_within_the_same_release(tmp_path: Path) -> None:
    company = Company.create(tmp_path, name="Example")
    write_front_desks(tmp_path, company.org)
    assert not refresh_front_desks(tmp_path, company.org)
    company.add_role("engineering", "tester", title="Tester", mission="Verify features.")
    _assert_desks(tmp_path, company)
    # An edit outside the API is noticed by the coordinator's shared refresh too.
    role = tmp_path / "org/roles/engineering/tester.yaml"
    data = yaml.safe_load(role.read_text(encoding="utf-8"))
    data["title"] = "Feature Tester"
    role.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert refresh_front_desks(tmp_path, company.org)
    assert not refresh_front_desks(tmp_path, company.org)
    for relative in RULE_PATHS:
        assert "Feature Tester" in (tmp_path / relative).read_text(encoding="utf-8")


def test_codex_project_config_parses_with_personal_settings_and_servers(tmp_path: Path) -> None:
    tomllib = pytest.importorskip("tomllib")
    company = Company.create(tmp_path / "company-\U0001f680", name="Example")
    folder = company.root / ".codex"
    folder.mkdir()
    config = folder / "config.toml"
    personal = 'model = "my-model"\n# Keep this comment.\n[mcp_servers.mine]\ncommand = "mine"\n'
    config.write_text(personal, encoding="utf-8")
    write_front_desks(company.root, company.org)
    write_front_desks(company.root, company.org)
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["model"] == "my-model" and parsed["mcp_servers"]["mine"] == {"command": "mine"}
    server = parsed["mcp_servers"]["trypro"]
    assert set(server) == {"command", "args"}
    assert all(isinstance(arg, str) for arg in server["args"])
    assert server["args"][-2:] == ["--client", "codex"]
    assert config.read_text(encoding="utf-8").startswith(personal)


def test_retiring_a_role_keeps_its_personal_notes_out_of_the_agent_directory(tmp_path: Path) -> None:
    from dataclasses import replace

    company = Company.create(tmp_path, name="Example")
    write_front_desks(tmp_path, company.org)
    agent = tmp_path / ".claude/agents/trypro-research-analyst.md"
    with agent.open("a", encoding="utf-8") as out:
        out.write("\nMy own note.\n")
    org = replace(company.org, roles={ref: r for ref, r in company.org.roles.items() if ref != "research/analyst"})
    write_front_desks(tmp_path, org)
    assert not agent.exists()
    assert (
        (tmp_path / ".claude/notes/trypro-research-analyst.md").read_text(encoding="utf-8").endswith("My own note.\n")
    )
    for relative in RULE_PATHS:
        assert "`research/analyst`" not in (tmp_path / relative).read_text(encoding="utf-8")


def test_codex_coordinator_opens_an_existing_company_and_refreshes_every_desk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from try_professional import cli
    from try_professional.adapters.mcp import server
    from try_professional.adapters.mcp.antigravity import write_antigravity_files

    company = Company.create(tmp_path, name="Example")
    result = company.run("Build a health check endpoint", dry_run=True)
    write_antigravity_files(tmp_path, company.org)
    seen: list[str] = []

    class Built:
        def run(self, transport: str) -> None:
            service = loader()
            try:
                assert service.client == "codex" and service.runtimes.default == "codex"
                assert service.run_status(result.run_id)["phase"] == "done"
                seen.append(service.org.company_name)
            finally:
                service.orchestrator.close()

    def build(company: Any, design: Any, **options: Any) -> Built:
        nonlocal loader
        loader = company
        assert options["coordinator"] and not options["person_tools"]
        return Built()

    loader: Any = None
    monkeypatch.delenv("TRYPRO_RUNTIME", raising=False)
    monkeypatch.setattr(server, "build_server", build)
    monkeypatch.setattr(cli, "use_embedding_service", lambda home: None)
    monkeypatch.setattr(cli, "_memory", lambda *a: None)
    monkeypatch.setattr(cli, "_skills", lambda *a: None)
    assert main(["mcp", "--coordinator", "--client", "codex", "--dir", str(tmp_path)]) == 0
    assert seen == ["Example"]
    _assert_desks(tmp_path, company)


@pytest.mark.parametrize("personal", PERSONAL_SERVERS)
def test_an_unmanaged_codex_server_is_reported_without_changing_any_front_desk(tmp_path: Path, personal: str) -> None:
    Company.create(tmp_path, name="Example")
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir()
    config.write_text(personal, encoding="utf-8")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = subprocess.run(
        [sys.executable, "-m", "try_professional.cli", "agents", "codex"],
        cwd=tmp_path,
        env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 2
    assert "unmanaged mcp_servers.trypro" in result.stdout + result.stderr
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_an_unmanaged_codex_server_outside_our_block_stops_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = Company.create(tmp_path, name="Example")
    write_front_desks(tmp_path, company.org)
    config = tmp_path / ".codex/config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n" + PERSONAL_SERVERS[1], encoding="utf-8")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.chdir(tmp_path)
    assert main(["agents", "antigravity"]) == 2
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_codex_setup_ignores_server_examples_in_personal_values_and_comments(tmp_path: Path) -> None:
    tomllib = pytest.importorskip("tomllib")
    company = Company.create(tmp_path, name="Example")
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir()
    personal = (
        "# [mcp_servers.trypro]\n"
        'example = """\n[mcp_servers.trypro]\ncommand = "personal"\n"""\n'
        "literal_example = '''\nmcp_servers = { trypro = {} }\n'''\n"
        'examples = [\n"[mcp_servers.trypro]",\n"trypro",\n]\n'
        '[profiles.mine]\nmcp_servers.trypro.command = "profile-server"\n'
    )
    config.write_text(personal, encoding="utf-8")
    write_front_desks(tmp_path, company.org)
    write_front_desks(tmp_path, company.org)
    text = config.read_text(encoding="utf-8")
    assert text.startswith(personal)
    parsed = tomllib.loads(text)
    assert parsed["profiles"]["mine"]["mcp_servers"]["trypro"]["command"] == "profile-server"
    assert parsed["mcp_servers"]["trypro"]["args"][-2:] == ["--client", "codex"]
