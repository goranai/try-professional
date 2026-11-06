"""The license gate. Unknown is rejected, and when sources disagree the stricter reading wins."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .frontmatter import read_skill_metadata
from .policy import Policy

LICENSE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "LICENCE.md", "LICENCE.txt", "COPYING")
NON_COMMERCIAL = re.compile(r"non[- ]?commercial|cc[- ]by[- ](sa[- ])?nc|polyform[- ]noncommercial", re.IGNORECASE)
PROPRIETARY = "PROPRIETARY"
NON_COMMERCIAL_ID = "NON-COMMERCIAL"

_ALIASES = {
    "mit": "MIT",
    "mit license": "MIT",
    "apache-2.0": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache2": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "isc": "ISC",
    "mpl-2.0": "MPL-2.0",
    "gpl-2.0": "GPL-2.0",
    "gpl-3.0": "GPL-3.0",
    "agpl-3.0": "AGPL-3.0",
    "lgpl-3.0": "LGPL-3.0",
    "unlicense": "Unlicense",
    "cc0-1.0": "CC0-1.0",
    "cc-by-4.0": "CC-BY-4.0",
    "proprietary": PROPRIETARY,
}


@dataclass(frozen=True)
class LicenseVerdict:
    spdx: str | None
    allowed: bool
    commercial_use: str  # allowed | restricted | unknown
    evidence: str
    reason: str
    sources: dict[str, str | None] = field(default_factory=dict)


def identify_text(text: str) -> str | None:
    """Recognize a license file by its distinctive wording. Not recognized means unknown."""
    lowered = " ".join(text.lower().split())
    if NON_COMMERCIAL.search(lowered):
        return NON_COMMERCIAL_ID
    if "gnu affero general public license" in lowered:
        return "AGPL-3.0"
    if "gnu lesser general public license" in lowered:
        return "LGPL-3.0"
    if "gnu general public license" in lowered:
        return "GPL-3.0" if "version 3" in lowered else "GPL-2.0"
    if "mozilla public license" in lowered:
        return "MPL-2.0"
    if "apache license" in lowered and "version 2.0" in lowered:
        return "Apache-2.0"
    if "permission is hereby granted, free of charge" in lowered:
        return "MIT"
    if "redistribution and use in source and binary forms" in lowered:
        return "BSD-3-Clause" if "neither the name" in lowered else "BSD-2-Clause"
    if "permission to use, copy, modify, and" in lowered and "with or without fee" in lowered:
        return "ISC"
    if "free and unencumbered software released into the public domain" in lowered:
        return "Unlicense"
    if "all rights reserved" in lowered:
        return PROPRIETARY
    return None


def identify_declared(value: str, policy: Policy) -> str | None:
    """Read a license: field. Handles an SPDX ID, an alias, or a simple OR or AND expression."""
    cleaned = value.strip()
    if NON_COMMERCIAL.search(cleaned):
        return NON_COMMERCIAL_ID
    for joiner, pick_any in ((" or ", True), (" and ", False)):
        if joiner in cleaned.lower():
            parts = re.split(joiner, cleaned.strip("()"), flags=re.IGNORECASE)
            ids = [identify_declared(part, policy) for part in parts]
            if any(i is None for i in ids):
                return None
            known = [i for i in ids if i]
            allowed = [i for i in known if i in policy.allowed_licenses]
            if pick_any:
                return allowed[0] if allowed else known[0]
            denied = [i for i in known if i not in policy.allowed_licenses]
            return denied[0] if denied else known[0]
    if cleaned.lower().startswith("proprietary"):
        return PROPRIETARY
    return _ALIASES.get(cleaned.lower())


def _read_license_file(folder: Path) -> str | None:
    for name in LICENSE_FILES:
        path = folder / name
        if path.is_file():
            return identify_text(path.read_text(encoding="utf-8", errors="replace")) or "UNRECOGNIZED"
    return None


def check_license(skill: Path, policy: Policy | None = None, *, repo_root: Path | None = None) -> LicenseVerdict:
    """Compare the skill's declared license, its own license file, and the repository's."""
    active = policy or Policy.default()
    sources: dict[str, str | None] = {}

    declared = read_skill_metadata(skill).license
    if declared:
        identified = identify_declared(declared, active)
        # "See LICENSE.txt" isn't a license. The file itself decides, and is read below.
        if identified or "licen" not in declared.lower():
            sources["frontmatter"] = identified or "UNRECOGNIZED"
    skill_file = _read_license_file(skill)
    if skill_file:
        sources["skill-license-file"] = skill_file
    if repo_root is not None:
        repo_file = _read_license_file(repo_root)
        if repo_file:
            sources["repo-license-file"] = repo_file

    return _decide(sources, active)


def _decide(sources: dict[str, str | None], policy: Policy) -> LicenseVerdict:
    evidence = ", ".join(sources) or "none"
    if not sources:
        return LicenseVerdict(None, False, "unknown", evidence, "No license was found.", sources)

    found = [value for value in sources.values() if value]
    if NON_COMMERCIAL_ID in found:
        return LicenseVerdict(
            NON_COMMERCIAL_ID, False, "restricted", evidence, "The license restricts commercial use.", sources
        )
    if PROPRIETARY in found:
        return LicenseVerdict(PROPRIETARY, False, "restricted", evidence, "The license is proprietary.", sources)
    # The nearest source decides the name, and a repository file is only a fallback for it.
    # A skill's own declaration can't widen a stricter license found anywhere else.
    if "UNRECOGNIZED" in found:
        known = [value for value in found if value != "UNRECOGNIZED"]
        spdx = known[0] if known else None
        return LicenseVerdict(spdx, False, "unknown", evidence, "A license could not be recognized.", sources)
    denied = [value for value in found if value not in policy.allowed_licenses]
    if denied:
        return LicenseVerdict(denied[0], False, "allowed", evidence, f"{denied[0]} isn't on the allowlist.", sources)
    disagreement = len(set(found)) > 1
    reason = "Sources name different allowed licenses." if disagreement else "On the allowlist."
    return LicenseVerdict(found[0], True, "allowed", evidence, reason, sources)
