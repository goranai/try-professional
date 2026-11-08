"""Pick the labelled sets' skills by a fixed rule, from three public repositories at pinned commits. Free.

usage: python sample.py <output.json>

The rule was fixed before any skill was read: alphabetical order of each repository's skill folders, then every
second or fourth one, as below. It prints each skill's name and frontmatter description, which is all a person needs
to label it and all the matcher reads.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

from skill_customs.frontmatter import parse_frontmatter

REPOS = {
    "addyosmani/agent-skills": "301b02b12bff98e4688e6984af6556802322bbde",
    "coreyhaines31/marketingskills": "96798cf38736ecdba015a1d2b0521b0c8f8f41dd",
    "minhnv0807/ai-business-skills": "4ef06ff9aa8bf5b6d065da9547e486991acab54d",
}


def _gh(path: str) -> object:
    return json.loads(subprocess.run(["gh", "api", path], capture_output=True, check=True, text=True).stdout)


def folders(repo: str, commit: str, only: str = "") -> list[str]:
    tree = _gh(f"repos/{repo}/git/trees/{commit}?recursive=1")
    paths = [e["path"][: -len("/SKILL.md")] for e in tree["tree"] if e["path"].endswith("/SKILL.md")]  # type: ignore[index]
    return sorted(p for p in paths if only in p)


def picks() -> dict[str, list[tuple[str, str]]]:
    addy = folders("addyosmani/agent-skills", REPOS["addyosmani/agent-skills"])
    corey = folders("coreyhaines31/marketingskills", REPOS["coreyhaines31/marketingskills"])
    business = folders("minhnv0807/ai-business-skills", REPOS["minhnv0807/ai-business-skills"], "/en/")
    return {
        "tune": [("addyosmani/agent-skills", p) for p in addy[0::2]]
        + [("coreyhaines31/marketingskills", p) for p in corey[0::4]],
        "heldout": [("addyosmani/agent-skills", p) for p in addy[1::2]]
        + [("coreyhaines31/marketingskills", p) for p in corey[2::4]]
        + [("minhnv0807/ai-business-skills", p) for p in business[0::5]],
    }


def frontmatter(repo: str, commit: str, path: str) -> dict[str, str]:
    data = _gh(f"repos/{repo}/contents/{path}/SKILL.md?ref={commit}")
    text = base64.b64decode(data["content"]).decode("utf-8")  # type: ignore[index]
    # The same reader the matcher uses, so a skill is labelled from what matching sees.
    meta = parse_frontmatter(text)
    return {
        "name": str(meta.get("name") or Path(path).name),
        "description": " ".join(str(meta.get("description") or "").split()),
    }


def main(out: Path) -> None:
    sets = {}
    for split, chosen in picks().items():
        sets[split] = [
            {"repo": repo, "commit": REPOS[repo], "path": path, **frontmatter(repo, REPOS[repo], path)}
            for repo, path in chosen
        ]
    out.write_text(json.dumps(sets, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for split, rows in sets.items():
        print(f"{split}: {len(rows)} skills")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
