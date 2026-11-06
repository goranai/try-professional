"""Skills agents propose after a run. They wait here for a person, and nothing reads them until then."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from skill_customs import scan


class ProposalWriter:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def propose(self, text: str, *, source: str) -> str:
        """Write a proposal, named by its content, with its scan result beside it."""
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        name = f"proposal-{digest}"
        folder = self.directory / name
        folder.mkdir(parents=True, exist_ok=True)
        front = yaml.safe_dump(
            {"name": name, "description": text.strip().splitlines()[0][:200] if text.strip() else name},
            allow_unicode=True,
            sort_keys=False,
        )
        (folder / "SKILL.md").write_text(f"---\n{front}---\n\n{text.strip()}\n", encoding="utf-8", newline="\n")
        report = scan(folder)
        summary = yaml.safe_dump(
            {
                "source": source,
                "scan_verdict": report.verdict,
                "findings": [f.to_dict() for f in report.findings],
                "next_step": f"Review it, then run: skill-customs add-local {folder.as_posix()} --license MIT",
            },
            allow_unicode=True,
            sort_keys=False,
        )
        (folder.parent / f"{name}.review.yaml").write_text(summary, encoding="utf-8", newline="\n")
        return name
