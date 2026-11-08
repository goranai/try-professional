"""Build a company for an agent to delegate to: the default departments, one custom role, and Claude Code's files.

usage: python build_company.py <folder>

No model is called and nothing is spent. Afterwards, open the folder in Claude Code and run /company <goal>, or call
delegate() in delegate.py from your own agent.
"""

from __future__ import annotations

import sys
from pathlib import Path

from try_professional import Company


def build(folder: Path) -> Company:
    company = Company.create(folder, name="Acme")
    # A custom role: it joins Marketing, reports to its head, and gets its own subagent in Claude Code.
    company.add_role(
        "marketing",
        "newsletter-editor",
        title="Newsletter Editor",
        mission="Write the weekly customer newsletter.",
        responsibilities=["Write the newsletter in the customer's language", "Cite a source for each claim"],
        skill_needs=["write email newsletters"],
        skill_categories=["content"],
    )
    company.setup_claude_code()
    return company


if __name__ == "__main__":
    built = build(Path(sys.argv[1] if len(sys.argv) > 1 else "acme"))
    print(f"{len(built.roles())} roles in {built.root}. In Claude Code there: /company <goal>")
