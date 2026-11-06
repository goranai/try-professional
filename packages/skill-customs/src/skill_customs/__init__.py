"""skill-customs: the lockfile, license, and pinning layer for agent skills.

Fetching never runs anything from a skill. A passing scan means no known signal was
found. It does not mean a skill is safe.
"""

from .attribution import render_attribution
from .discover import Candidate, SkillFile, discover, list_skill_paths, search_skill_files
from .errors import (
    ApprovalError,
    FetchError,
    LicenseRejected,
    LockfileError,
    NotASkill,
    SkillCustomsError,
    UnsafeArchive,
)
from .fetch import GitHubClient, is_full_sha
from .frontmatter import SkillMetadata, read_skill_metadata
from .hashing import content_hash
from .install import PinResult, approve, pin, pin_local, remove, skill_dir, verify
from .licenses import LicenseVerdict, check_license
from .lockfile import Approval, LockEntry, Lockfile
from .policy import Policy
from .scan import (
    BuiltinScanner,
    CommandScanner,
    Finding,
    Scanner,
    ScanReport,
    decide_trust,
    scan,
)

__version__ = "0.1.0.dev0"

__all__ = [
    "Approval",
    "ApprovalError",
    "BuiltinScanner",
    "Candidate",
    "CommandScanner",
    "FetchError",
    "Finding",
    "GitHubClient",
    "LicenseRejected",
    "LicenseVerdict",
    "LockEntry",
    "Lockfile",
    "LockfileError",
    "NotASkill",
    "PinResult",
    "Policy",
    "ScanReport",
    "Scanner",
    "SkillCustomsError",
    "SkillFile",
    "SkillMetadata",
    "UnsafeArchive",
    "__version__",
    "approve",
    "check_license",
    "content_hash",
    "decide_trust",
    "discover",
    "is_full_sha",
    "list_skill_paths",
    "pin",
    "pin_local",
    "read_skill_metadata",
    "remove",
    "render_attribution",
    "scan",
    "search_skill_files",
    "skill_dir",
    "verify",
]
