from pathlib import Path

import pytest

from try_professional import Company
from try_professional.adapters.agent_runner import (
    ANTIGRAVITY,
    CLAUDE_CODE,
    CODEX,
    AgentRunner,
    AgentSettings,
    RuntimePlan,
)
from try_professional.adapters.api_runner.tools import ToolContext, run_tool
from try_professional.adapters.mcp import McpService
from try_professional.core import scratch as storage
from try_professional.core.audit import read_records, verify_log
from try_professional.core.deliverables import export_deliverables, export_drafts
from try_professional.core.listing import brief_listing
from try_professional.core.orchestrator import Orchestrator
from try_professional.core.pipeline import HumanDecided, InvocationFailed
from try_professional.core.ports import NullMemory, NullSkills


def test_each_run_owns_scratch_outside_its_workspace(org, tmp_path: Path) -> None:
    engine = Orchestrator(org, tmp_path / "home")
    try:
        first, _ = engine.start("first")
        second, _ = engine.start("second")
        scratch = engine.scratch(first)
        assert scratch.is_dir()
        assert scratch != engine.scratch(second)
        assert not scratch.is_relative_to(engine.workspace(first))
        (scratch / "clone").mkdir()
        (scratch / "clone" / "README.md").write_text("temporary clone")
        reopened = Orchestrator(org, engine.home)
        try:
            assert reopened.scratch(first) == scratch
        finally:
            reopened.close()
        engine.cancel(first)
        assert not scratch.exists()
        assert engine.scratch(second).is_dir()
        engine.cancel(second)
    finally:
        engine.close()


@pytest.mark.parametrize("outcome", ["done", "failed", "cancelled"])
@pytest.mark.parametrize("retained", [False, True])
def test_terminal_runs_clean_only_their_scratch_and_audit_the_result(
    org, harness, outcome: str, retained: bool
) -> None:
    run = harness(org)
    engine = run.orchestrator
    engine.keep_run_scratch = retained
    run_id, actions = engine.start("finish")
    scratch = engine.scratch(run_id)
    (scratch / "clone.txt").write_text("temporary")
    neighbour_id, _ = engine.start("neighbour")
    neighbour = engine.scratch(neighbour_id)
    deliverable = engine.workspace(run_id) / "result.txt"
    deliverable.write_text("keep")
    if outcome == "done":
        original = run.client.complete

        def complete(request):
            assert str(scratch) in request.messages[0]["content"]
            return original(request)

        run.client.complete = complete
        result = run.runner.resume(run_id)
        assert result.status == "done"
    elif outcome == "failed":
        engine.handle(
            run_id, InvocationFailed(invocation_id=actions[0].invocation_id, reason="cannot run", retryable=False)
        )
        request = engine.open_actions(run_id)[0]
        engine.handle(run_id, HumanDecided(request_id=request.request_id, decision="abandon", decided_by="person"))
        assert engine.state(run_id).phase.value == "failed"
    else:
        engine.cancel(run_id)
        assert engine.state(run_id).cancelled
    assert scratch.exists() == retained
    assert neighbour.is_dir()
    assert deliverable.read_text() == "keep"
    record = [r for r in read_records(engine.audit_path(run_id)) if r["kind"] == "scratch_cleanup"]
    assert record[-1]["data"] == {
        "path": str(scratch),
        "status": outcome,
        "outcome": "retained" if retained else "deleted",
    }
    assert verify_log(engine.audit_path(run_id)).ok
    engine.cancel(neighbour_id)
    engine.close()


def test_a_cleanup_failure_is_audited_without_undoing_cancellation(org, tmp_path: Path, monkeypatch) -> None:
    engine = Orchestrator(org, tmp_path / "home")
    run_id, _ = engine.start("finish")

    def locked(*args, **kwargs):
        raise PermissionError("file is in use")

    monkeypatch.setattr(storage.shutil, "rmtree", locked)
    engine.cancel(run_id)
    assert engine.state(run_id).cancelled
    assert engine.scratch(run_id).exists()
    record = read_records(engine.audit_path(run_id))[-1]
    assert record["kind"] == "scratch_cleanup"
    assert record["data"]["outcome"] == "failed"
    assert record["data"]["error"] == "PermissionError"
    assert record["data"]["reason"] == "file is in use"
    engine.close()


@pytest.mark.parametrize("retained", [False, True])
@pytest.mark.parametrize("alias", [False, True])
def test_scratch_and_virtualenvs_never_cross_listing_export_or_later_run_boundaries(
    org, tmp_path: Path, retained: bool, alias: bool
) -> None:
    engine = Orchestrator(org, tmp_path / "home", keep_run_scratch=retained)
    run_id, _ = engine.start("finish")
    scratch = engine.scratch(run_id)
    (scratch / "clone").mkdir()
    (scratch / "clone" / "source.py").write_text("temporary clone")
    (scratch / "environment").mkdir()
    (scratch / "environment" / "pyvenv.cfg").write_text("home = python")
    (scratch / "environment" / "dependency.py").write_text("temporary dependency")
    workspace = engine.workspace(run_id)
    if alias:
        _link_directory(workspace / "temporary", scratch)
    (workspace / "result.txt").write_text("deliverable")
    service = McpService(engine, bound_role=org.root_role)
    invocation_id = engine.open_actions(run_id)[0].invocation_id
    assert service.workspace_list(run_id, invocation_id) == '["result.txt"]'
    assert brief_listing(workspace) == ["result.txt"]
    # Even if a person points an input or listing boundary at scratch's parent, its contents stay behind.
    assert not any(".trypro-scratch" in name for name in brief_listing(scratch.parent.parent))
    assert brief_listing(scratch) == []
    for exporter in (export_deliverables, export_drafts):
        target = tmp_path / exporter.__name__
        exporter(engine.state(run_id), workspace, target, {})
        assert (target / "files" / "result.txt").read_text() == "deliverable"
        assert not any(p.name in {"source.py", "dependency.py", "pyvenv.cfg"} for p in target.rglob("*"))
    engine.cancel(run_id)
    assert scratch.exists() == retained
    records = read_records(engine.audit_path(run_id))
    assert next(r for r in reversed(records) if r["kind"] == "scratch_cleanup")["data"]["outcome"] == (
        "retained" if retained else "deleted"
    )
    later = Orchestrator(
        org,
        tmp_path / "next-home",
        inputs_dir=workspace if alias else scratch.parent,
        deliverables_dir=tmp_path / "export_deliverables",
    )
    later_id, _ = later.start("next")
    assert not any(p.name in {"source.py", "dependency.py", "pyvenv.cfg"} for p in later.workspace(later_id).rglob("*"))
    assert not later.scratch(later_id).is_relative_to(workspace)
    later.cancel(later_id)
    later.close()
    engine.close()


def test_api_shell_receives_the_runs_scratch_instead_of_an_inherited_value(org, tmp_path: Path, monkeypatch) -> None:
    engine = Orchestrator(org, tmp_path / "home")
    run_id, _ = engine.start("shell")
    monkeypatch.setenv("TRYPRO_RUN_SCRATCH", "another run")
    context = ToolContext(
        role=org.root_role,
        allowed=("shell",),
        workspace=engine.workspace(run_id),
        memory=NullMemory(),
        skills=NullSkills(),
        scratch=engine.scratch(run_id),
    )
    python = str(Path(__import__("sys").executable)).replace("\\", "/")
    command = f'"{python}" -c "import os; print(os.environ[\'TRYPRO_RUN_SCRATCH\'])"'
    result = run_tool(context, "shell", {"command": command})
    assert str(engine.scratch(run_id)) in result
    assert "exit code 0" in result, result
    service = McpService(engine, bound_role=org.root_role)
    action = service.run_next_action(run_id)["actions"][0]
    assert str(engine.scratch(run_id)) in action["assignment"]
    assert action["environment"] == {"TRYPRO_RUN_SCRATCH": str(engine.scratch(run_id))}
    engine.cancel(run_id)
    engine.close()


@pytest.mark.parametrize("runtime", [CODEX, ANTIGRAVITY, CLAUDE_CODE])
def test_every_agent_step_receives_scratch_in_its_prompt_environment_and_directory(org, harness, runtime: str) -> None:
    from .test_agents import _fake_agents

    run = harness(org)
    fake = _fake_agents(run, [])
    seen = []

    def capture(argv, stdin, env, cwd, timeout):
        prompt = stdin or argv[argv.index("-p") + 1]
        run_id = prompt.rsplit("run_id: ", 1)[1].strip()
        scratch = run.orchestrator.scratch(run_id)
        assert str(scratch) in prompt
        assert env["TRYPRO_RUN_SCRATCH"] == str(scratch)
        assert not cwd.is_relative_to(scratch)
        assert ["--add-dir", str(scratch)] in [argv[i : i + 2] for i in range(len(argv) - 1)]
        seen.append(run_id)
        return fake(argv, stdin, env, cwd, timeout)

    runner = AgentRunner(
        run.orchestrator,
        RuntimePlan(default=runtime),
        AgentSettings(codex="codex", antigravity="agy", claude="claude", gate=False),
        root=run.orchestrator.home,
        run_process=capture,
        environ={"TRYPRO_RUN_SCRATCH": "wrong"},
    )
    result = runner.run("finish")
    assert result.status == "done"
    assert len(seen) > 1
    assert not run.orchestrator.scratch(result.run_id).exists()
    run.orchestrator.close()


def test_the_persons_retention_setting_reaches_library_and_cli_engines(tmp_path: Path, monkeypatch) -> None:
    import argparse

    from try_professional import cli

    company = Company.create(tmp_path / "company", name="Test")
    with (company.root / "trypro.yaml").open("a", encoding="utf-8") as config:
        config.write("\nkeep_run_scratch: true\n")
    engine = company._orchestrator()
    run_id, _ = engine.start("keep")
    engine.cancel(run_id)
    assert engine.scratch(run_id).is_dir()
    engine.close()
    monkeypatch.chdir(company.root)
    _, engine = cli._runner(argparse.Namespace(dry_run=True))
    assert engine.keep_run_scratch
    engine.close()


@pytest.mark.parametrize("replacement", ["scratch", "parent", "child"])
def test_cleanup_never_follows_a_link_or_junction_outside_the_runs_scratch(
    org, tmp_path: Path, replacement: str
) -> None:
    engine = Orchestrator(org, tmp_path / "home")
    run_id, _ = engine.start("finish")
    scratch = engine.scratch(run_id)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("outside")
    if replacement == "parent":
        scratch.rmdir()
        scratch.parent.rmdir()
        link = scratch.parent
    elif replacement == "scratch":
        scratch.rmdir()
        link = scratch
    else:
        link = scratch / "redirected-clone"
    _link_directory(link, outside)
    engine.cancel(run_id)
    assert marker.read_text() == "outside"
    record = next(r for r in reversed(read_records(engine.audit_path(run_id))) if r["kind"] == "scratch_cleanup")
    assert record["data"]["outcome"] == ("deleted" if replacement == "child" else "failed")
    assert not scratch.exists() if replacement == "child" else link.exists()
    engine.close()


def _link_directory(link: Path, target: Path) -> None:
    import os
    import subprocess
    import sys

    if sys.platform == "win32":
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, timeout=10)
        assert done.returncode == 0, done.stderr
    else:
        os.symlink(target, link, target_is_directory=True)


def test_cleanup_removes_a_clones_readonly_files(org, tmp_path: Path) -> None:
    import stat

    engine = Orchestrator(org, tmp_path / "home")
    run_id, _ = engine.start("finish")
    scratch = engine.scratch(run_id)
    clone = scratch / "clone" / ".git" / "objects"
    clone.mkdir(parents=True)
    object_file = clone / "object"
    object_file.write_text("git object")
    object_file.chmod(stat.S_IREAD)
    engine.cancel(run_id)
    assert not scratch.exists()
    record = next(r for r in reversed(read_records(engine.audit_path(run_id))) if r["kind"] == "scratch_cleanup")
    assert record["data"]["outcome"] == "deleted"
    engine.close()


def test_already_absent_scratch_has_a_clear_cleanup_record(org, tmp_path: Path) -> None:
    engine = Orchestrator(org, tmp_path / "home")
    run_id, _ = engine.start("finish")
    engine.scratch(run_id).rmdir()
    engine.cancel(run_id)
    record = next(r for r in reversed(read_records(engine.audit_path(run_id))) if r["kind"] == "scratch_cleanup")
    assert record["data"]["outcome"] == "already_absent"
    assert not engine.scratch(run_id).exists()
    engine.close()


def test_a_system_temp_alias_does_not_redirect_run_ownership(tmp_path: Path) -> None:
    import subprocess
    import sys

    company = Company.create(tmp_path / "company", name="Test")
    target = tmp_path / "system-temp"
    target.mkdir()
    alias = tmp_path / "temp-alias"
    _link_directory(alias, target)
    other = tmp_path / "other-temp"
    other.mkdir()
    marker = other / "keep.txt"
    marker.write_text("outside")
    code = """
import sys, tempfile
tempfile.tempdir = sys.argv[1]
from try_professional import Company
engine = Company.open(sys.argv[2])._orchestrator()
try:
    run_id, _ = engine.start('finish')
    scratch = engine.scratch(run_id)
    assert scratch.is_dir()
    tempfile.tempdir = sys.argv[3]
    assert engine.scratch(run_id) == scratch
    engine.cancel(run_id)
    assert not scratch.exists()
    print(scratch)
finally:
    engine.close()
"""
    done = subprocess.run(
        [sys.executable, "-c", code, str(alias), str(company.root), str(other)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    assert str(target.resolve() / storage.SCRATCH_FOLDER) in done.stdout
    assert marker.read_text() == "outside"


def test_an_active_agent_allows_the_engine_to_clean_run_scratch(org, harness) -> None:
    import subprocess
    import sys

    from try_professional.adapters.agent_runner import run_agent_step

    run = harness(org)
    engine = run.orchestrator
    run_id, actions = engine.start("finish")

    def active_agent(argv, stdin, env, cwd, timeout):
        # A real process holds its current directory open on Windows until it exits.
        with subprocess.Popen(
            [sys.executable, "-c", "print('ready', flush=True); input()"],
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as process:
            try:
                assert process.stdout.readline().strip() == "ready"
                engine.cancel(run_id)
                assert not engine.scratch(run_id).exists()
            finally:
                process.communicate(input="stop\n", timeout=10)
        return 0, ""

    try:
        run_agent_step(
            engine,
            actions[0],
            run_id,
            CODEX,
            root=engine.home,
            plan=RuntimePlan(default=CODEX),
            settings=AgentSettings(codex="codex", gate=False),
            run_process=active_agent,
            environ={},
        )
    finally:
        engine.close()


def test_an_existing_active_run_gets_scratch_when_a_role_resumes_it(org, tmp_path: Path, monkeypatch) -> None:
    engine = Orchestrator(org, tmp_path / "home")
    # A persisted run from before this feature has no scratch allocation record or folder.
    with monkeypatch.context() as legacy:
        legacy.setattr(engine, "_allocate_scratch", lambda run_id: None)
        run_id, _ = engine.start("continue")
    engine.close()
    resumed = Orchestrator(org, tmp_path / "home")
    try:
        action = McpService(resumed, bound_role=org.root_role).run_next_action(run_id)["actions"][0]
        path = Path(action["environment"]["TRYPRO_RUN_SCRATCH"])
        assert path.is_dir()
        assert str(path) in action["assignment"]
        assert any(r["kind"] == "scratch_allocated" for r in read_records(resumed.audit_path(run_id)))
        resumed.cancel(run_id)
        assert not path.exists()
    finally:
        resumed.close()


def test_a_new_role_process_reuses_the_runs_original_scratch_allocation(tmp_path: Path) -> None:
    import subprocess
    import sys

    company = Company.create(tmp_path / "company", name="Test")
    engine = company._orchestrator()
    run_id, _ = engine.start("continue")
    original = engine.scratch(run_id)
    engine.close()
    other_temp = tmp_path / "other-temp"
    other_temp.mkdir()
    marker = other_temp / "keep.txt"
    marker.write_text("outside")
    code = """
import sys, tempfile
tempfile.tempdir = sys.argv[1]
from try_professional import Company
engine = Company.open(sys.argv[2])._orchestrator()
try:
    scratch = engine.scratch(sys.argv[3])
    assert scratch.is_dir()
    print(scratch)
    engine.cancel(sys.argv[3])
finally:
    engine.close()
"""
    done = subprocess.run(
        [sys.executable, "-c", code, str(other_temp), str(company.root), run_id],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == str(original)
    assert not original.exists()
    assert marker.read_text() == "outside"


def test_a_swapped_allocation_record_cannot_delete_another_runs_scratch(org, tmp_path: Path) -> None:
    import json

    engine = Orchestrator(org, tmp_path / "home")
    first, _ = engine.start("first")
    second, _ = engine.start("second")
    first_path, second_path = engine.scratch(first), engine.scratch(second)
    record = engine.home / "runs" / first / "scratch.json"
    record.write_text(json.dumps({"path": str(second_path)}), encoding="utf-8")
    engine.cancel(first)
    assert first_path.exists()
    assert second_path.exists()
    outcome = next(r for r in reversed(read_records(engine.audit_path(first))) if r["kind"] == "scratch_cleanup")
    assert outcome["data"]["outcome"] == "failed"
    engine.cancel(second)
    engine.close()


def test_cancel_refuses_a_substituted_scratch_parent_with_the_runs_owner_name(tmp_path: Path) -> None:
    import json
    import subprocess
    import sys

    company = Company.create(tmp_path / "company", name="Test")
    engine = company._orchestrator()
    try:
        run_id, _ = engine.start("continue")
        original = engine.scratch(run_id)
        record = storage.record_path(engine.home, run_id)
        unowned = tmp_path / "unowned-data" / storage.SCRATCH_FOLDER / original.name
        unowned.mkdir(parents=True)
        marker = unowned / "keep.txt"
        marker.write_text("outside")
        record.write_text(json.dumps({"path": str(unowned)}), encoding="utf-8")
    finally:
        engine.close()

    done = subprocess.run(
        [sys.executable, "-m", "try_professional.cli", "cancel", run_id],
        cwd=company.root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    assert marker.read_text() == "outside"
    assert original.is_dir()
    reopened = company._orchestrator()
    try:
        assert reopened.state(run_id).cancelled
        outcome = next(r for r in reversed(read_records(reopened.audit_path(run_id))) if r["kind"] == "scratch_cleanup")
        assert outcome["data"]["outcome"] == "failed"
        assert "cleanup refused" in outcome["data"]["reason"]
        assert reopened.verify_audit(run_id).ok
        record.write_text(json.dumps({"path": str(original)}), encoding="utf-8")
        assert reopened.scratch(run_id) == original
    finally:
        reopened.close()
