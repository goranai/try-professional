"""Department packs: removable departments that ship with the package.

Installed is not invoked. A pack makes a department available, and the plan or a
gate decides whether it ever runs.
"""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ..org.loader import dump_org, read_documents

if TYPE_CHECKING:
    from importlib.abc import Traversable

ROOT_PACK = "executive"
DEFAULT_GATES: dict[str, Any] = {
    "legal": {
        "reviewer": "legal/counsel",
        "triggers": ["public_facing", "touches_license", "touches_personal_data"],
        "on_unavailable": "block",
    }
}
DEFAULT_PIPELINE: dict[str, Any] = {
    "max_parallel_tasks": 4,
    "max_rework": 2,
    "max_invalid_outputs": 2,
    "working_language": "auto",
    "budget": {"max_tokens_per_run": 200_000, "on_exceeded": "escalate"},
}


def _pack_root(name: str) -> Traversable:
    root = resources.files("try_professional.packs").joinpath(name)
    if not root.joinpath("pack.yaml").is_file():
        raise KeyError(f"unknown pack '{name}'; available: {', '.join(available_packs())}")
    return root


def available_packs() -> list[str]:
    root = resources.files("try_professional.packs")
    names = [p.name for p in root.iterdir() if p.is_dir() and p.joinpath("pack.yaml").is_file()]
    return sorted(names, key=lambda n: (n != ROOT_PACK, n))


def pack_department(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(_pack_root(name).joinpath("pack.yaml").read_text(encoding="utf-8"))
    return loaded


def pack_role(name: str, role_id: str) -> dict[str, Any]:
    text = _pack_root(name).joinpath("roles").joinpath(f"{role_id}.yaml").read_text(encoding="utf-8")
    loaded: dict[str, Any] = yaml.safe_load(text)
    return loaded


def _copy_roles(name: str, org_dir: Path) -> None:
    target = org_dir / "roles" / name
    target.mkdir(parents=True, exist_ok=True)
    for item in _pack_root(name).joinpath("roles").iterdir():
        if item.name.endswith(".yaml"):
            (target / item.name).write_text(item.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")


def compose_org(company: str, packs: list[str]) -> dict[str, Any]:
    if ROOT_PACK not in packs:
        packs = [ROOT_PACK, *packs]
    return {
        "spec": "org/v1",
        "company": {"name": company},
        "root_role": "executive/ceo",
        "departments": [pack_department(name) for name in packs],
        "gates": yaml.safe_load(yaml.safe_dump(DEFAULT_GATES)),
        "pipeline": yaml.safe_load(yaml.safe_dump(DEFAULT_PIPELINE)),
    }


def install_org(org_dir: Path, company: str, packs: list[str]) -> None:
    org = compose_org(company, packs)
    for department in org["departments"]:
        _copy_roles(department["id"], org_dir)
    dump_org(org, org_dir)


def add_pack(org_dir: Path, name: str) -> None:
    org, _ = read_documents(org_dir)
    if any(d["id"] == name for d in org["departments"]):
        raise ValueError(f"department '{name}' is already installed")
    org["departments"].append(pack_department(name))
    _copy_roles(name, org_dir)
    dump_org(org, org_dir)


def remove_pack(org_dir: Path, name: str) -> None:
    if name == org_root_department(org_dir):
        raise ValueError(f"'{name}' holds the root role and cannot be removed")
    org, _ = read_documents(org_dir)
    remaining = [d for d in org["departments"] if d["id"] != name]
    if len(remaining) == len(org["departments"]):
        raise ValueError(f"department '{name}' is not installed")
    org["departments"] = remaining
    dump_org(org, org_dir)
    shutil.rmtree(org_dir / "roles" / name, ignore_errors=True)


def org_root_department(org_dir: Path) -> str:
    org, _ = read_documents(org_dir)
    root: str = org["root_role"]
    return root.split("/", 1)[0]
