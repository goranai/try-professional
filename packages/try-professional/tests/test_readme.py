"""The README people see on PyPI is the root README, with links that work there."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("package_readme", ROOT / "scripts" / "package_readme.py")
assert spec is not None and spec.loader is not None
package_readme: Any = importlib.util.module_from_spec(spec)
sys.modules["package_readme"] = package_readme
spec.loader.exec_module(package_readme)


def test_the_packaged_readme_is_up_to_date() -> None:
    assert package_readme.main(["--check"]) == 0, "Run: python scripts/package_readme.py"


def test_images_point_at_raw_files_and_links_at_pages() -> None:
    text = package_readme.packaged("![demo](docs/assets/demo.svg) [guide](docs/x.md) [faq](#faq) [pypi](https://p)")
    assert "raw.githubusercontent.com/goranai/try-professional/main/docs/assets/demo.svg?sanitize=true" in text
    assert "https://github.com/goranai/try-professional/blob/main/docs/x.md" in text
    assert "[faq](#faq)" in text and "[pypi](https://p)" in text


def test_every_local_link_in_the_readme_exists() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for target in re.findall(r"\]\((?!https?://|#)([^)#]+)", text):
        assert (ROOT / target).exists(), target
