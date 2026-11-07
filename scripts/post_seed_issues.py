"""Create the labels and issues in .github/seed-issues.yaml. Prints them, and changes nothing, unless given --post.

usage: python scripts/post_seed_issues.py [--post] [--repo owner/name]

Needs the GitHub CLI when posting. Issues whose title already exists are skipped, so it's safe to run twice.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "goranai/try-professional"
LABEL_COLOR = "0e8a16"


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, encoding="utf-8").stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--post", action="store_true", help="Really create them")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    args = parser.parse_args()
    issues = yaml.safe_load((ROOT / ".github" / "seed-issues.yaml").read_text(encoding="utf-8"))
    labels = sorted({label for issue in issues for label in issue["labels"]})
    if not args.post:
        print(f"Would create {len(labels)} labels and {len(issues)} issues in {args.repo}:")
        for issue in issues:
            print(f"  [{', '.join(issue['labels'])}] {issue['title']}")
        return
    existing = {
        i["title"] for i in json.loads(_gh("issue", "list", "-R", args.repo, "--limit", "500", "--json", "title"))
    }
    for label in labels:
        _gh("label", "create", label, "-R", args.repo, "--color", LABEL_COLOR, "--force")
    for issue in issues:
        if issue["title"] in existing:
            print(f"Skipped, it exists: {issue['title']}")
            continue
        flags = [item for label in issue["labels"] for item in ("--label", label)]
        print(
            _gh("issue", "create", "-R", args.repo, "--title", issue["title"], "--body", issue["body"], *flags).strip()
        )


if __name__ == "__main__":
    main()
