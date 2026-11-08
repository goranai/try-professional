"""Prompt size is a tracked number. Offline, using the scripted model.

These budgets were set from a measurement, with a small allowance. Growing past one should be a
decision, so raise a budget only with a reason. They don't predict cost: a real run adds replies,
retries, and tool calls.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("measure_prompts", ROOT / "scripts" / "measure_prompts.py")
assert spec and spec.loader
measure_prompts: Any = importlib.util.module_from_spec(spec)
sys.modules["measure_prompts"] = measure_prompts
spec.loader.exec_module(measure_prompts)

# Raised on 2026-11-07 from 28146 for wake-up briefs, playbooks and fuller task briefs: Phase 4c's cost.
# Raised the same day from 34000 for what a company can reach. Without it, a real plan required web sources from a
# company with no web, and a command spent over 50,000 tokens to end blocked. About 200 tokens per large run.
# Raised on 2026-11-08 from 34700 for a computer: the shell and web_fetch, given to every step that does or checks work.
# Raised on 2026-11-09 from 36400 because prompts now say that roles have that computer. They used to say "no shell,
# no network", so planners didn't ask for work a role could do, such as searching GitHub.
# Then to 37600, telling planners that a held step counts as done: without it, a real run looped, reviewers asking for
# an issue to exist that only a person could post.
# And to 37800 for the same rule at intake, where a real CEO still wrote "the issue is opened" as a criterion.
# And to 38500 in 0.6.2, for rules against passing work around: the fewest roles, no handing work back, and saying
# what another department has before asking it. One avoided step costs far more than this.
TOTAL_CHAR_BUDGET = 38500


def test_a_minimal_run_stays_within_its_prompt_budget() -> None:
    steps = measure_prompts.measure()
    assert [s.purpose for s in steps] == [
        "intake",
        "plan",
        "dispatch",
        "execute",
        "review",
        "integrate",
        "retrospective",
    ]
    total = sum(s.total for s in steps)
    assert total <= TOTAL_CHAR_BUDGET, f"prompts grew to {total} characters"
