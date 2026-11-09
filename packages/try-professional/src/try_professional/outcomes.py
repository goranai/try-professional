"""Following what a company put out, and learning from what happened to it.

Learning works the same for every kind of work: a verdict, with its reason, becomes a lesson for every role that worked
on it. Where code can read the verdict, a checker does, with no model; GitHub issues and pull requests, through `gh`,
are the one that ships. Anything else, such as what a client said, a person reports, from the terminal or from chat.

GitHub issues and pull requests are checked with `gh`, with no model. A change of state becomes a lesson for every role
that worked on it. A new reply from someone else becomes a follow-up command, which the company runs like any other:
it reads the thread, and answers only if an answer is needed. Anything else, such as a post or an email, a person
settles with `trypro outcomes set`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .core.orchestrator import Orchestrator, utc_now
from .core.outside import Outcome

Gh = Callable[[list[str]], str]
IGNORED_AFTER_DAYS = 30
CHECKED_BY = "outcome check"
_GITHUB = re.compile(r"https://github\.com/([^/]+)/([^/]+)/(issues|pull)/(\d+)")
_BOT = re.compile(r"(\[bot\]$|^github-actions$|^dependabot)", re.IGNORECASE)
FOLLOW_UP_MARK = "Follow up on"


class GhUnavailable(RuntimeError):
    """gh isn't installed or signed in, so GitHub links can't be checked."""


def run_gh(args: list[str]) -> str:
    gh = shutil.which("gh")
    if gh is None:
        raise GhUnavailable("gh isn't installed. GitHub links can't be checked: https://cli.github.com")
    done = subprocess.run(
        [gh, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False
    )
    if done.returncode != 0:
        message = (done.stderr or done.stdout).strip().splitlines()
        raise GhUnavailable(message[-1] if message else f"gh {' '.join(args[:2])} failed")
    return done.stdout


@dataclass
class CheckReport:
    checked: int = 0
    settled: list[Outcome] = field(default_factory=list)
    follow_ups: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _replies(data: dict[str, Any], me: str) -> list[dict[str, Any]]:
    """What other people wrote: comments, and a pull request's reviews. Our own and bots' don't count."""
    items = list(data.get("comments") or []) + [r for r in data.get("reviews") or [] if (r.get("body") or "").strip()]
    replies = []
    for item in items:
        login = str((item.get("author") or {}).get("login") or "")
        if login and login.lower() != me.lower() and not _BOT.search(login):
            replies.append(item)
    return replies


def _labels(data: dict[str, Any]) -> set[str]:
    return {str(label.get("name", "")).lower() for label in data.get("labels") or []}


_BY_DESIGN = re.compile(
    r"by design|as designed|as intended|working as intended|works as intended|intended behaviou?r|expected behaviou?r"
    r"|not a bug|is intentional|on purpose|that's how it'?s? (?:meant|supposed)",
    re.IGNORECASE,
)
_LOW_IMPACT = re.compile(
    r"low priority|low impact|not worth|too minor|very minor|cosmetic|nitpick|edge case|nice to have|not important",
    re.IGNORECASE,
)
_ISSUE_REF = re.compile(r"(?:https://github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+))|(?<![\w/])#(\d+)\b")


def judge(kind: str, data: dict[str, Any], said: str = "") -> tuple[str, str]:
    """A status from GitHub's own record, and why. 'open' while it's still being decided. `said` is the maintainer's
    last word, which tells "that's how it's meant to work" from "not worth it", when the labels don't."""
    state = str(data.get("state") or "").upper()
    reason = str(data.get("stateReason") or "").upper()
    labels = _labels(data)
    label_text = " ".join(labels)
    if kind == "pull" and (state == "MERGED" or data.get("mergedAt")):
        return "accepted", "merged"
    if state != "CLOSED":
        return "open", ""
    if reason == "DUPLICATE" or "duplicate" in labels:
        return "duplicate", "closed as a duplicate"
    if _BY_DESIGN.search(label_text) or (reason != "COMPLETED" and _BY_DESIGN.search(said)):
        return "by_design", "closed: it works as intended"
    if _LOW_IMPACT.search(label_text) or (reason != "COMPLETED" and _LOW_IMPACT.search(said)):
        return "low_impact", "closed: real, and not worth acting on"
    if kind == "issue" and reason == "COMPLETED" and not labels & {"invalid", "wontfix", "won't fix", "spam"}:
        return "accepted", "closed as completed"
    named = sorted(labels & {"invalid", "wontfix", "won't fix", "spam", "not planned"})
    return "rejected", f"closed{f' as {named[0]}' if named else ' without being accepted'}"


def duplicate_of(url: str, replies: list[dict[str, Any]]) -> str:
    """The issue it duplicates, from what was said when it was closed: a link, or #123 in the same repository."""
    match = _GITHUB.match(url)
    if match is None:
        return ""
    own = int(match.group(4))
    for reply in reversed(replies):
        for repo, number, short in _ISSUE_REF.findall(str(reply.get("body") or "")):
            target = int(number or short)
            if target == own:
                continue
            where = repo or f"{match.group(1)}/{match.group(2)}"
            return f"https://github.com/{where}/issues/{target}"
    return ""


_CREATE = re.compile(r"\bgh\s+issue\s+create\b")
_REPO_FLAG = re.compile(r"(?:--repo|-R)(?:\s+|=)['\"]?([\w.-]+/[\w.-]+)")
_TITLE_FLAG = re.compile(r"""(?:--title|-t)(?:\s+|=)(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|(\S+))""")
SIMILAR_SHOWN = 5


def possible_duplicates(command: str, *, gh: Gh = run_gh) -> str:
    """Before a person decides on a new issue: what that repository already has, open or closed, that's close to it.
    It searches with the issue's own title, with no model. "" if it isn't a new issue, or nothing close was found."""
    repo, title = _REPO_FLAG.search(command), _TITLE_FLAG.search(command)
    if not _CREATE.search(command) or repo is None or title is None:
        return ""
    words = " ".join(re.findall(r"[\w'-]{3,}", next(g for g in title.groups() if g)))[:200]
    # A title is worded one way, and an earlier report or fix another: "docs: fix dead links in README" for a broken
    # license link. The exact link, path, or code the issue is about finds those, so they're searched as well.
    queries = [(words, "its title"), *((f'"{term}"', term) for term in _exact_terms(command, repo.group(1)))]
    found: dict[int, tuple[dict[str, Any], str]] = {}
    for query, why in queries:
        try:
            items = json.loads(gh(["search", "issues", query, "--repo", repo.group(1), "--include-prs", "--limit",
                                   str(SIMILAR_SHOWN), "--json", "number,title,state,url"]))  # fmt: skip
        except (GhUnavailable, json.JSONDecodeError, ValueError):
            continue
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and isinstance(item.get("number"), int):
                found.setdefault(item["number"], (item, why))
    lines = [f"#{i.get('number')} [{str(i.get('state', '')).lower()}] {i.get('title', '')} {i.get('url', '')} "
             f"(found by {why})" for i, why in found.values()]  # fmt: skip
    return ("Already in " + repo.group(1) + ", maybe the same thing:\n" + "\n".join(lines)) if lines else ""


EXACT_TERMS = 3


def _exact_terms(command: str, repo: str) -> list[str]:
    """What an issue is precisely about, from its text: the links it names, without the repository's own address,
    and code in backticks. Short or generic ones are left out."""
    terms: dict[str, None] = {}
    for url in re.findall(r"https?://[^\s'\"<>)\]]+", command):
        path = re.sub(r"^https?://(www\.)?", "", url).rstrip(".,;:")
        path = re.sub(rf"^github\.com/{re.escape(repo)}/?", "", path, flags=re.IGNORECASE)
        if len(path) >= 8 and "/" in path:
            terms.setdefault("/".join(path.split("/")[-3:]), None)
    for code in re.findall(r"`([^`\n]{6,60})`", command):
        if not code.isalpha():
            terms.setdefault(code.strip(), None)
    return list(terms)[:EXACT_TERMS]


_MAINTAINER = {"OWNER", "MEMBER", "COLLABORATOR"}


def _telling_reply(replies: list[dict[str, Any]], status: str) -> dict[str, Any] | None:
    """The reply worth learning from: a maintainer's last word. Anyone's, only when it was turned down and no
    maintainer spoke. Nothing for accepted work, where the last comment is often noise left long after."""
    maintainers = [r for r in replies if str(r.get("authorAssociation") or "").upper() in _MAINTAINER]
    if maintainers:
        return maintainers[-1]
    return replies[-1] if replies and status in ("rejected", "duplicate", "by_design", "low_impact") else None


def _quote(reply: dict[str, Any] | None) -> str:
    if not reply:
        return ""
    login = (reply.get("author") or {}).get("login", "someone")
    body = " ".join(str(reply.get("body") or "").split())
    return f'{login}: "{body[:160]}{"…" if len(body) > 160 else ""}"'


def follow_up_goal(item: Outcome, new: int) -> str:
    title = f" ('{item.title}')" if item.title else ""
    return (
        f"{FOLLOW_UP_MARK} the {item.kind} this company sent to {item.target or 'them'}, {item.ref}{title}. {new} "
        f"new {'reply' if new == 1 else 'replies'} from others since it was last checked. Read all of it, and the "
        "rules of the place it went. Answer only if an answer is needed: a question asked of us, a request for "
        "details or a change, or something we should confirm. Keep it short, and in their language. If nothing is "
        "needed, send nothing, and say so. Sending needs a person's approval."
    )


def check(
    orchestrator: Orchestrator,
    *,
    gh: Gh = run_gh,
    now: Callable[[], str] = utc_now,
    ignored_after_days: int = IGNORED_AFTER_DAYS,
) -> CheckReport:
    """Check every open GitHub link this company put out. Safe to run as often as you like."""
    report = CheckReport()
    links = [item for item in orchestrator.outside.outcomes("open") if _GITHUB.match(item.url)]
    if not links:
        return report
    try:
        me = gh(["api", "user", "--jq", ".login"]).strip()
    except GhUnavailable as error:
        report.problems.append(str(error))
        return report
    queued = " ".join(entry["goal"] for entry in orchestrator.company.queued())
    for item in links:
        match = _GITHUB.match(item.url)
        assert match is not None
        kind = "pull" if match.group(3) == "pull" else "issue"
        fields = "state,mergedAt,labels,comments,reviews" if kind == "pull" else "state,stateReason,labels,comments"
        try:
            data = json.loads(gh([kind if kind == "issue" else "pr", "view", item.url, "--json", fields]))
        except (GhUnavailable, json.JSONDecodeError) as error:
            report.problems.append(f"{item.url}: {error}")
            continue
        report.checked += 1
        replies = _replies(data, me)
        telling = _telling_reply(replies, "rejected")
        status, why = judge(kind, data, str((telling or {}).get("body") or ""))
        if status == "duplicate":
            original = duplicate_of(item.url, replies)
            why = f"closed as a duplicate of {original}" if original else why
        if status == "open":
            age = datetime.fromisoformat(now()) - datetime.fromisoformat(item.created_at)
            if not replies and age >= timedelta(days=ignored_after_days):
                status, why = "ignored", f"no reply in {ignored_after_days} days"
        if status != "open":
            note = "; ".join(part for part in (why, _quote(_telling_reply(replies, status))) if part)
            settled = orchestrator.settle_outcome(item.id, status, note, by=CHECKED_BY)
            if settled is not None:
                report.settled.append(settled)
        elif len(replies) > item.seen_replies and item.url not in queued:
            orchestrator.company.add_queued(follow_up_goal(item, len(replies) - item.seen_replies), now())
            report.follow_ups.append(item.url)
        orchestrator.outside.saw_replies(item.id, len(replies), now())
    return report
