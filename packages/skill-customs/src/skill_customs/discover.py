"""Find candidate skill repositories. This nominates: nothing is installed, and every gate still applies.

Stars can be bought, so ranking can't vouch for anything. It only decides what gets looked at first.
"""

from __future__ import annotations

import math
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .fetch import GitHubClient
from .policy import Policy

DEFAULT_TOP = 40
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
