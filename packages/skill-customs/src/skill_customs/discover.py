"""Find candidate skill repositories. This nominates: nothing is installed, and every gate still applies.

Stars can be bought, so ranking can't vouch for anything. It only decides what gets looked at first.
"""

from __future__ import annotations

import math
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .errors import FetchError
from .fetch import GitHubClient, check_repo, is_full_sha
from .policy import Policy

SKILL_FILE = "SKILL.md"
DEFAULT_TOP = 40
DEFAULT_REPOS_PER_SEARCH = 5
DEFAULT_SKILLS_PER_REPO = 3
PAGE_SIZE = 100
MIN_AGE_DAYS = 30
MAX_IDLE_DAYS = 365


@dataclass(frozen=True)
class Candidate:
    repo: str
    url: str
    description: str
    stars: int
    pushed_at: str
    license_spdx: str | None
    license_allowed: bool
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _days_since(stamp: str, now: datetime) -> float:
    moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return (now - moment).total_seconds() / 86400


def score_repository(item: dict[str, Any], now: datetime) -> float:
    """Stars count on a log scale, so a huge repository can't drown out everything else.

    Recency fades over a year. A high ratio of open issues to stars lowers the score, as a
    rough sign of unanswered problems.
    """
    stars = int(item.get("stargazers_count") or 0)
    recency = max(0.0, 1.0 - _days_since(item["pushed_at"], now) / MAX_IDLE_DAYS)
    issues = int(item.get("open_issues_count") or 0)
    maintenance = 1.0 - min(1.0, issues / (stars + 10))
    return round(math.log10(stars + 1) + 2.0 * recency + maintenance, 4)


def rank_repositories(
    items: list[dict[str, Any]], policy: Policy, *, now: datetime, top: int = DEFAULT_TOP
) -> list[Candidate]:
    """Drop archived, forked, brand-new, abandoned, and unlicensed repositories, then rank the rest."""
    candidates = []
    for item in items:
        if item.get("archived") or item.get("fork"):
            continue
        if _days_since(item["created_at"], now) < MIN_AGE_DAYS or _days_since(item["pushed_at"], now) > MAX_IDLE_DAYS:
            continue
        spdx = (item.get("license") or {}).get("spdx_id")
        if not spdx or spdx == "NOASSERTION":
            continue
        candidates.append(
            Candidate(
                repo=item["full_name"],
                url=item["html_url"],
                description=(item.get("description") or "")[:200],
                stars=int(item.get("stargazers_count") or 0),
                pushed_at=item["pushed_at"],
                license_spdx=spdx,
                license_allowed=spdx in policy.allowed_licenses,
                score=score_repository(item, now),
            )
        )
    candidates.sort(key=lambda c: (-c.score, c.repo))
    return candidates[:top]


@dataclass(frozen=True)
class SkillFile:
    """One skill found by content search. It's a nomination, like a Candidate."""

    repo: str
    path: str
    license_spdx: str | None
    license_allowed: bool
    stars: int
    score: float


def search_skill_files(
    query: str,
    *,
    client: GitHubClient | None = None,
    policy: Policy | None = None,
    now: datetime | None = None,
    max_repos: int = DEFAULT_REPOS_PER_SEARCH,
    max_per_repo: int = DEFAULT_SKILLS_PER_REPO,
) -> list[SkillFile]:
    """Find skills by what their SKILL.md says, including those deep inside large collections.

    Repository search only sees a repository's own description, so it misses most skills. Code
    search needs a token, and GitHub allows few of these per minute. Repositories still go through
    the same filters as discover(): no archives, forks, brand-new, abandoned, or unlicensed ones.
    """
    github = client or GitHubClient()
    encoded = urllib.parse.quote(f"{query} filename:{SKILL_FILE}")
    data = github.get_json(f"/search/code?q={encoded}&per_page={PAGE_SIZE}")
    items = data.get("items", []) if isinstance(data, dict) else []
    by_repo: dict[str, list[str]] = {}
    for item in items:
        repo = (item.get("repository") or {}).get("full_name")
        path = str(item.get("path", ""))
        if repo and path.split("/")[-1] == SKILL_FILE:
            by_repo.setdefault(repo, []).append(path[: -len(SKILL_FILE)].rstrip("/"))
    active = policy or Policy.default()
    moment = now or datetime.now(timezone.utc)
    found: list[SkillFile] = []
    for repo, paths in list(by_repo.items())[:max_repos]:
        ranked = rank_repositories([github.get_json(f"/repos/{repo}")], active, now=moment, top=1)
        if not ranked:
            continue
        candidate = ranked[0]
        found += [
            SkillFile(repo, path, candidate.license_spdx, candidate.license_allowed, candidate.stars, candidate.score)
            for path in sorted(paths)[:max_per_repo]
        ]
    return found


def list_skill_paths(repo: str, commit: str, *, client: GitHubClient | None = None) -> list[str]:
    """The folder of every SKILL.md at one commit, from the file listing. Nothing is downloaded or run."""
    github = client or GitHubClient()
    check_repo(repo)
    if not is_full_sha(commit):
        raise FetchError(f"'{commit}' isn't a full 40-character commit SHA.")
    data = github.get_json(f"/repos/{repo}/git/trees/{commit}?recursive=1")
    tree = data.get("tree", []) if isinstance(data, dict) else []
    paths = []
    for item in tree:
        path = str(item.get("path", ""))
        if item.get("type") == "blob" and (path == SKILL_FILE or path.endswith(f"/{SKILL_FILE}")):
            paths.append(path[: -len(SKILL_FILE)].rstrip("/"))
    return sorted(paths)


def discover(
    query: str,
    *,
    client: GitHubClient | None = None,
    policy: Policy | None = None,
    top: int = DEFAULT_TOP,
    now: datetime | None = None,
) -> list[Candidate]:
    """Search GitHub for one category's query. Run it on whatever schedule you like: no daemon runs here."""
    github = client or GitHubClient()
    encoded = urllib.parse.quote(f"{query} fork:false archived:false")
    data = github.get_json(f"/search/repositories?q={encoded}&sort=stars&order=desc&per_page={PAGE_SIZE}")
    items = data.get("items", []) if isinstance(data, dict) else []
    return rank_repositories(items, policy or Policy.default(), now=now or datetime.now(timezone.utc), top=top)
