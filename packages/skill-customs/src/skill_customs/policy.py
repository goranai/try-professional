"""What this project accepts. Defaults work with no config file."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_LICENSES = ("MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC")
DEFAULT_FILE_NAME = "skill-customs.yaml"


@dataclass(frozen=True)
class Policy:
    allowed_licenses: tuple[str, ...] = DEFAULT_LICENSES
    max_file_bytes: int = 2_000_000
    max_skill_bytes: int = 50_000_000
    max_files: int = 2_000
    max_archive_bytes: int = 500_000_000
    # Extra scanners from installed packages, by entry point name. None run unless listed here.
    scanners: tuple[str, ...] = ()
    # External scanner commands, by name. The skill's folder is added as the last argument.
    command_scanners: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def default(cls) -> Policy:
        return cls()

    @classmethod
    def load(cls, path: Path | None = None) -> Policy:
        """Read skill-customs.yaml if it exists. A file that was named explicitly must exist."""
        target = path or Path(DEFAULT_FILE_NAME)
        if not target.is_file():
            if path is not None:
                raise FileNotFoundError(f"no policy file at {path}")
            return cls()
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        known = {"allowed_licenses", "max_file_bytes", "max_skill_bytes", "max_files", "max_archive_bytes"}
        known |= {"scanners", "command_scanners"}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown policy settings: {', '.join(sorted(unknown))}")
        values = {key: value for key, value in data.items() if key not in ("command_scanners",)}
        for key in ("allowed_licenses", "scanners"):
            if key in values:
                values[key] = tuple(values[key])
        commands = {name: tuple(argv) for name, argv in (data.get("command_scanners") or {}).items()}
        return cls(command_scanners=commands, **values)
