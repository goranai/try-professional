"""Install the pre-commit checks and the pre-push secret scan into this clone's .git/hooks."""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    hooks = Path(
        subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--git-path", "hooks"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    if not hooks.is_absolute():
        hooks = ROOT / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    for name in ("pre-commit", "pre-push"):
        target = hooks / name
        shutil.copyfile(ROOT / "scripts" / "hooks" / name, target)
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"Installed {target}")


if __name__ == "__main__":
    main()
