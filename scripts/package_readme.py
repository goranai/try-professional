"""Write packages/try-professional/README.md from the root README, with links that work on PyPI.

usage: python scripts/package_readme.py [--check]

PyPI shows the package's README with no repository around it, so a relative link would go nowhere. Pages and files link
to GitHub, and images to their raw file, so they still show. With --check, it changes nothing, and fails if the copy is
out of date.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/goranai/try-professional"
RAW = "https://raw.githubusercontent.com/goranai/try-professional/main"
_IMAGE = re.compile(r"!\[([^\]]*)\]\((?!https?://)([^)]+)\)")
_LINK = re.compile(r"(?<!!)\[([^\]]*)\]\((?!https?://|#)([^)]+)\)")


def packaged(text: str) -> str:
    # GitHub serves a raw SVG as text unless asked to sanitize it, and then it shows as an image.
    text = _IMAGE.sub(lambda m: f"![{m[1]}]({RAW}/{m[2]}{'?sanitize=true' if m[2].endswith('.svg') else ''})", text)
    return _LINK.sub(lambda m: f"[{m[1]}]({REPO}/blob/main/{m[2]})", text)


def main(argv: list[str]) -> int:
    wanted = packaged((ROOT / "README.md").read_text(encoding="utf-8"))
    target = ROOT / "packages" / "try-professional" / "README.md"
    if "--check" in argv:
        if target.read_text(encoding="utf-8") != wanted:
            print("packages/try-professional/README.md is out of date. Run: python scripts/package_readme.py")
            return 1
        return 0
    target.write_text(wanted, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
