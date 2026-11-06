"""Regenerate spec/artifacts-v1/ from the Pydantic models, and sync the packaged spec copies."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from try_professional.core.artifacts import artifact_schemas

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    target = ROOT / "spec" / "artifacts-v1"
    target.mkdir(parents=True, exist_ok=True)
    for name, schema in artifact_schemas().items():
        text = json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        (target / name).write_text(text, encoding="utf-8", newline="\n")
    packaged = ROOT / "packages" / "try-professional" / "src" / "try_professional" / "_spec"
    for name in ("org-v1.schema.json", "role-v1.schema.json"):
        shutil.copyfile(ROOT / "spec" / name, packaged / name)


if __name__ == "__main__":
    main()
