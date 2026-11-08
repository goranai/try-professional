"""The pipeline, split by concern, keeps the rules the single file had."""

from __future__ import annotations

import re
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1] / "src" / "try_professional" / "core" / "pipeline"
BANNED = re.compile(
    r"\b(shorts|youtube|tiktok|e-?commerce|saas|catering|hotel|podcast|real estate|law firm|umkm|video)\b",
    re.IGNORECASE,
)


def test_no_part_of_the_pipeline_names_an_industry() -> None:
    """Industry knowledge belongs in templates. This covers every module, now the pipeline isn't one file."""
    for path in PIPELINE.glob("*.py"):
        found = BANNED.findall(path.read_text(encoding="utf-8"))
        assert not found, f"{path.name} names an industry: {found}"


def test_no_module_does_io_or_calls_a_model() -> None:
    """The pipeline is a pure function of state and event. Anything that reads files or the clock lives elsewhere."""
    forbidden = re.compile(r"^\s*(import|from)\s+(os|pathlib|time|datetime|subprocess|httpx|requests|socket)\b", re.M)
    for path in PIPELINE.glob("*.py"):
        assert not forbidden.search(path.read_text(encoding="utf-8")), path.name


def test_each_module_is_a_manageable_size() -> None:
    for path in PIPELINE.glob("*.py"):
        assert len(path.read_text(encoding="utf-8").splitlines()) < 500, path.name
