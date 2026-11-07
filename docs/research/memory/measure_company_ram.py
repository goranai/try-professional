"""Peak RAM of a company over MCP, from the operating system, with one bound server per role. Free: no model calls.

usage:
    python measure_company_ram.py <work_dir> <output.json>

Each server really recalls memory through the real path: MCP, then the memory rules, then Mnemosyne, then the embedding
model. It measures three things:

    typical:   the operator, one role, and the service. Agents run one at a time, so this is an ordinary command.
    all roles: a server for every role at once, with the shared embedding service. The worst case.
    no service: TRYPRO_EMBED_SERVICE=off, the old way, with a few servers only, because a whole company of these
               could take the machine down. The total for every role is that per-server cost times the roles, and
               is reported as computed, not measured.

Memory is the unique set size summed over each server and its children, so a page two processes share isn't counted
twice, and the process's resident size is kept beside it. Needs psutil and the mcp extra. Neither is needed to run the
company without MCP.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import psutil
from mcp import Client, StdioServerParameters

from try_professional.adapters.mcp.claude_code import server_config
from try_professional.cli import main as trypro
from try_professional.core.orchestrator import Orchestrator
from try_professional.core.pipeline import Purpose
from try_professional.core.pipeline.state import Invocation
from try_professional.org import load_org

OLD_WAY_SERVERS = 3
SERVICE_FILE = "embed-service.json"
MIN_FREE_MB_TO_ADD_A_SERVER = 2500


def _family(pid: int | None) -> set[int]:
    if pid is None:
        return set()
    try:
        process = psutil.Process(pid)
        return {pid, *(child.pid for child in process.children(recursive=True))}
    except psutil.Error:
        return set()


def _sizes(pids: list[int], skip: set[int] | None = None) -> tuple[int, int]:
    """(unique, resident) in MB, across these processes and their children, leaving out skip."""
    unique = resident = 0
    seen: set[int] = set(skip or ())
    for pid in pids:
        try:
            parent = psutil.Process(pid)
            family = [parent, *parent.children(recursive=True)]
        except psutil.Error:
            continue
        for process in family:
            if process.pid in seen:
                continue
            seen.add(process.pid)
            try:
                info = process.memory_full_info()
            except psutil.Error:
                continue
            unique += info.uss
            resident += info.rss
    return round(unique / 1048576), round(resident / 1048576)


def _service_pid(home: Path) -> int | None:
    try:
        return int(json.loads((home / SERVICE_FILE).read_text(encoding="utf-8"))["pid"])
    except (OSError, ValueError, KeyError):
        return None


def _stop_service(home: Path) -> None:
    pid = _service_pid(home)
    if pid is None:
        return
    try:
        process = psutil.Process(pid)
        for each in [*process.children(recursive=True), process]:
            each.terminate()
    except psutil.Error:
        pass
    (home / SERVICE_FILE).unlink(missing_ok=True)


def _prepare(root: Path) -> tuple[str, dict[str, str]]:
    """A company, and a run with open work for every role, so each one's server can really use its memory."""
    root.mkdir(parents=True, exist_ok=True)
    before = Path.cwd()
    os.chdir(root)
    try:
        if not (root / "org" / "org.yaml").is_file():
            trypro(["init", "--skip-model"])
    finally:
        os.chdir(before)
    org = load_org(root / "org")
    company = Orchestrator(org, root / ".trypro")
    run_id, _ = company.start("Measure memory")
    state = company.state(run_id)
    work: dict[str, str] = {}
    for number, role in enumerate(sorted(org.roles)):
        invocation_id = f"inv-measure-{number}"
        state.invocations[invocation_id] = Invocation(
            id=invocation_id, role=role, purpose=Purpose.EXECUTE, output_type="TaskReport"
        )
        work[role] = invocation_id
    company.ledger.save(state, company._log(run_id).head, company.clock())
    company.close()
    return run_id, work


class Server:
    def __init__(self, root: Path, role: str | None, environment: dict[str, str]) -> None:
        config = server_config(root, role)
        self.role = role
        self.parameters = StdioServerParameters(command=config["command"], args=config["args"], env=environment)
        self.client: Any = None
        self.pid = 0

    async def __aenter__(self) -> Server:
        before = {p.pid for p in psutil.Process().children(recursive=True)}
        self.client = Client(self.parameters)
        await self.client.__aenter__()
        fresh = [p for p in psutil.Process().children(recursive=True) if p.pid not in before]
        self.pid = fresh[0].pid if fresh else 0
        return self

    async def __aexit__(self, *details: Any) -> None:
        await self.client.__aexit__(*details)

    async def recall(self, run_id: str, invocation_id: str) -> str:
        answer = await self.client.call_tool(
            "memory_recall", {"run_id": run_id, "invocation_id": invocation_id, "query": "weekly lunch menu"}
        )
        return str(answer.content[0].text)


async def _open_servers(root: Path, roles: list[str], run_id: str, work: dict[str, str], env: dict[str, str]) -> Any:
    """Opens a server per role and has each recall memory, stopping early rather than endangering the machine."""
    results: dict[str, Any] = {"opened": 0, "stopped_early": False, "per_server_unique_mb": []}
    open_servers: list[Server] = []
    service_home = root / ".trypro"
    try:
        for role in roles:
            if psutil.virtual_memory().available / 1048576 < MIN_FREE_MB_TO_ADD_A_SERVER:
                results["stopped_early"] = True
                break
            server = Server(root, role, env)
            await server.__aenter__()
            open_servers.append(server)
            answer = await server.recall(run_id, work[role])
            if "untrusted" not in answer:
                raise RuntimeError(f"{role} couldn't recall: {answer[:200]}")
            results["opened"] += 1
            # The server that started the service is its parent, so it's left out here, and counted once below.
            skip = _family(_service_pid(service_home))
            results["per_server_unique_mb"].append(_sizes([server.pid], skip)[0])
        pids = [server.pid for server in open_servers]
        service = _service_pid(service_home)
        servers_unique, servers_resident = _sizes(pids, _family(service))
        service_unique, service_resident = _sizes([service]) if service else (0, 0)
        results.update(
            servers_unique_mb=servers_unique,
            servers_resident_mb=servers_resident,
            service_unique_mb=service_unique,
            service_resident_mb=service_resident,
            total_unique_mb=servers_unique + service_unique,
        )
    finally:
        for server in reversed(open_servers):
            await server.__aexit__(None, None, None)
    return results


async def _typical(root: Path, roles: list[str], run_id: str, work: dict[str, str], env: dict[str, str]) -> Any:
    """An ordinary command: the operator's server is always open, and one role works at a time."""
    config = server_config(root)
    parameters = StdioServerParameters(command=config["command"], args=config["args"], env=env)
    before = {p.pid for p in psutil.Process().children(recursive=True)}
    peak = {"total_unique_mb": 0}
    async with Client(parameters):
        operator = [p.pid for p in psutil.Process().children(recursive=True) if p.pid not in before]
        for role in roles[:5]:
            async with Server(root, role, env) as server:
                await server.recall(run_id, work[role])
                service = _service_pid(root / ".trypro")
                total = _sizes([*operator, server.pid, *([service] if service else [])])[0]
                peak["total_unique_mb"] = max(peak["total_unique_mb"], total)
    return peak


async def measure(work_dir: Path) -> dict[str, Any]:
    root = work_dir / "company"
    run_id, work = _prepare(root)
    roles = sorted(work)
    shared = {**os.environ, "TRYPRO_MEMORY_CHECK": "off"}
    shared.pop("TRYPRO_EMBED_SERVICE", None)
    old = {**shared, "TRYPRO_EMBED_SERVICE": "off"}
    results: dict[str, Any] = {"roles": len(roles), "free_mb_at_start": round(psutil.virtual_memory().available / 1048576)}

    results["old_way"] = await _open_servers(root, roles[:OLD_WAY_SERVERS], run_id, work, old)
    per_server = results["old_way"]["per_server_unique_mb"]
    results["old_way"]["computed_for_every_role_mb"] = round(sum(per_server) / max(1, len(per_server)) * len(roles))

    started = time.monotonic()
    results["typical"] = await _typical(root, roles, run_id, work, shared)
    results["typical"]["seconds"] = round(time.monotonic() - started)
    results["all_roles_shared"] = await _open_servers(root, roles, run_id, work, shared)
    _stop_service(root / ".trypro")
    return results


def main() -> None:
    output = Path(sys.argv[2])
    results = asyncio.run(measure(Path(sys.argv[1]).resolve()))
    output.write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
