"""Find skills for a proposed company, and say plainly which roles have none.

Everything happens inside the proposal folder. Downloads go through skill-customs: a full commit
SHA, the license allowlist, and a scan, and nothing from a skill is run. Only skill_needs phrases
go to GitHub, never the business description.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from skill_customs import (
    FetchError,
    GitHubClient,
    LicenseRejected,
    Lockfile,
    Policy,
    SkillCustomsError,
    pin,
    search_skill_files,
)

from ..memory.embedding import Embedder
from ..org import Org
from ..org.policy import EXECUTIVE, LEGAL
from ..skills import (
    MATCH_THRESHOLD,
    UNCATEGORIZED,
    Candidate,
    Decisions,
    RerankCache,
    RerankReport,
    SkillCatalog,
    Verdict,
    clean_line,
    exclusion_reason,
    records_from_lock,
    rerank,
    shortlist,
)
from ..skills.catalog import SkillRecord
from ..skills.loadouts import DUPLICATE_SIMILARITY, SHORT_DESCRIPTION

GAP_SIMILARITY = 0.35
QA = "qa"
MAX_QUERIES = 20
MAX_SKILLS = 60
# A killed run can't clean up after itself, and nothing here lives this long.
STALE_TEMP_SECONDS = 24 * 60 * 60
_TEMP_PREFIXES = ("trypro-skills-", "trypro-estimate-", "skill-customs-")
# GitHub allows about 10 code searches a minute.
SECONDS_BETWEEN_SEARCHES = 6.5
TIME_LIMIT_SECONDS = 180
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "the",
        "of",
        "for",
        "to",
        "in",
        "on",
        "with",
        "into",
        "from",
        "by",
        "or",
        "your",
        "their",
        "its",
        "as",
        "at",
        "be",
        "is",
        "are",
        "that",
        "this",
    ]
)


@dataclass
class RoleMatch:
    skill_id: str
    name: str
    description: str
    score: float
    reason: str
    category: str
    category_source: str  # declared | proposed | person
    judged_by: str  # model | embeddings
    content_sha256: str


@dataclass
class ProvisionResult:
    matches: dict[str, list[RoleMatch]] = field(default_factory=dict)
    gaps: dict[str, list[str]] = field(default_factory=dict)
    quarantined: list[dict[str, str]] = field(default_factory=list)
    cannot_run: list[dict[str, str]] = field(default_factory=list)
    discovery: list[str] = field(default_factory=list)
    rerank: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProvisionResult:
        result = cls(**{k: v for k, v in data.items() if k != "matches"})
        result.matches = {
            role: [RoleMatch(**item) for item in items] for role, items in (data.get("matches") or {}).items()
        }
        return result


class _SharedTarballs(GitHubClient):
    """Download a repository once, however many skills come from it."""

    def __init__(self, cache: Path) -> None:
        super().__init__()
        self._cache = cache

    def download_tarball(self, repo: str, commit: str, target: Path, policy: Policy) -> None:
        cached = self._cache / f"{repo.replace('/', '__')}-{commit}.tar.gz"
        if not cached.is_file():
            super().download_tarball(repo, commit, cached, policy)
        shutil.copyfile(cached, target)


def search_terms(phrase: str, *, limit: int = 4) -> str:
    """The phrase's main words, because GitHub requires every word in a search to match."""
    words = [w for w in re.findall(r"[a-z0-9-]+", phrase.lower()) if w not in _STOPWORDS]
    return " ".join(words[:limit])


def _normalized(vector: Any) -> Any:
    length = float(np.linalg.norm(vector))
    return vector / length if length else vector


def _all_records(project_lock: Lockfile, project: Path, lock: Lockfile, dest: Path, decisions: Decisions) -> list[Any]:
    return [
        *records_from_lock(project_lock, project / "skills", decisions),
        *records_from_lock(lock, dest, decisions),
    ]


def needs_without_a_skill(org: Org, catalog: SkillCatalog, embedder: Embedder) -> list[str]:
    """skill_needs nothing in the catalog comes close to. These are what discovery looks for."""
    vectors = [vector for _, vector in catalog.eligible()]
    missing: list[str] = []
    for role in org.roles.values():
        for need in role.skill_needs:
            if need in missing:
                continue
            query = embedder.embed_query(need)
            best = max((float(query @ vector) for vector in vectors), default=0.0)
            if best < GAP_SIMILARITY:
                missing.append(need)
    return missing


def needs_in_search_order(org: Org, needs: list[str]) -> list[str]:
    """One need from each role in turn, working departments first, so a limit still covers every role."""
    fixed = (EXECUTIVE, LEGAL, QA)
    roles = sorted(org.roles.values(), key=lambda role: (role.department in fixed, role.ref))
    queues = [[need for need in role.skill_needs if need in needs] for role in roles]
    ordered: list[str] = []
    while any(queues):
        for queue in queues:
            if queue:
                need = queue.pop(0)
                if need not in ordered:
                    ordered.append(need)
    return ordered


def remove_stale_temp_folders(temp: Path | None = None, *, now: float | None = None) -> None:
    """Remove folders that an earlier run was killed before deleting. Never touches anything else."""
    folder = temp or Path(tempfile.gettempdir())
    cutoff = (now if now is not None else time.time()) - STALE_TEMP_SECONDS
    try:
        for path in folder.iterdir():
            if path.name.startswith(_TEMP_PREFIXES) and path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
    except OSError:
        return


def discover_skills(
    needs: list[str],
    lock: Lockfile,
    dest: Path,
    *,
    client: GitHubClient | None = None,
    say: Callable[[str], None] = lambda text: None,
    time_limit: float = TIME_LIMIT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    """Pin what GitHub has for these needs. A slow or failed search leaves gaps, and never fails init."""
    if client is None and not os.environ.get("GITHUB_TOKEN"):
        return ["Skipped skill discovery: GITHUB_TOKEN isn't set. Set it to search GitHub for skills."]
    remove_stale_temp_folders()
    report: list[str] = []
    queries = list(dict.fromkeys(q for q in (search_terms(need) for need in needs) if q))[:MAX_QUERIES]
    say(f"Searching GitHub with {len(queries)} skill phrases. The business description isn't sent.")
    started = clock()
    repos: set[str] = set()
    commits: dict[str, str] = {}
    counts = {"pinned": 0, "license": 0, "other": 0}
    with tempfile.TemporaryDirectory(prefix="trypro-skills-", ignore_cleanup_errors=True) as work:
        github = client or _SharedTarballs(Path(work))
        try:
            for number, query in enumerate(queries):
                if clock() - started > time_limit or counts["pinned"] >= MAX_SKILLS:
                    report.append("Stopped skill discovery at its limit, so some needs weren't searched.")
                    break
                if number:
                    sleep(SECONDS_BETWEEN_SEARCHES)
                for found in search_skill_files(query, client=github):
                    repos.add(found.repo)
                    skill_id = "/".join(part for part in (found.repo, found.path) if part)
                    if skill_id in lock.entries or counts["pinned"] >= MAX_SKILLS:
                        continue
                    if not found.license_allowed:
                        counts["license"] += 1
                        continue
                    commit = commits.get(found.repo) or github.resolve_ref(found.repo, "HEAD")
                    commits[found.repo] = commit
                    try:
                        result = pin(found.repo, commit, path=found.path, dest=dest, client=github)
                    except LicenseRejected:
                        counts["license"] += 1
                        continue
                    except FetchError:
                        raise
                    except (SkillCustomsError, OSError):
                        # An OSError here is usually a path too long for Windows. One skill can't stop init.
                        counts["other"] += 1
                        continue
                    lock.add(result.entry)
                    counts["pinned"] += 1
        except FetchError as error:
            report.append(f"Stopped skill discovery early: {error}")
    report.insert(
        0,
        f"Skill discovery: {len(queries)} searches, {len(repos)} repositories, {counts['pinned']} skills pinned, "
        f"{counts['license']} refused for their license, {counts['other']} unusable.",
    )
    return report


def _unique(
    candidates: list[tuple[Candidate, Verdict | None]], vectors: dict[str, Any]
) -> list[tuple[Candidate, Verdict | None]]:
    kept: list[tuple[Candidate, Verdict | None]] = []
    for item in candidates:
        vector = vectors[item[0].record.skill_id]
        if all(float(vector @ vectors[other[0].record.skill_id]) < DUPLICATE_SIMILARITY for other in kept):
            kept.append(item)
    return kept


def _to_match(
    candidate: Candidate, verdict: Verdict | None, categories: tuple[str, ...], decisions: Decisions
) -> RoleMatch:
    record: SkillRecord = candidate.record
    if decisions.category_for(record.skill_id):
        category, source = record.category, "person"
    elif record.category != UNCATEGORIZED:
        category, source = record.category, "declared"
    else:
        category = (verdict.category if verdict else "") or (categories[0] if categories else UNCATEGORIZED)
        source = "proposed"
    reason = verdict.reason if verdict else f'close to "{candidate.need[:50]}"'
    return RoleMatch(
        skill_id=record.skill_id,
        name=clean_line(record.name, 60),
        description=clean_line(record.description, SHORT_DESCRIPTION),
        score=round(candidate.similarity, 3),
        reason=clean_line(reason),
        category=category,
        category_source=source,
        judged_by="model" if verdict else "embeddings",
        content_sha256=record.content_sha256,
    )


def _flagged(org: Org, lock: Lockfile, records: list[SkillRecord], result: ProvisionResult) -> None:
    for skill_id, entry in sorted(lock.entries.items()):
        if entry.trust == "quarantined":
            found = entry.scan.get("findings") or []
            findings = [f for f in found if isinstance(f, dict) and f.get("rule") != "executable-content"]
            # Scripts alone are a limit of this version. Any other finding is a warning about the skill itself.
            if not findings and entry.scan.get("executable_files"):
                reason = "ships scripts, which need a shell, and no role has one"
                result.cannot_run.append({"skill_id": skill_id, "reason": reason})
                continue
            first = findings[0] if findings else {}
            why = first.get("message") or first.get("rule") or "the scan flagged it"
            result.quarantined.append({"skill_id": skill_id, "reason": clean_line(why)})
    for record in records:
        if record.trust == "quarantined":
            continue
        reasons = [exclusion_reason(role, record, allow_uncategorized=True) for role in org.roles.values()]
        if all(reasons):
            result.cannot_run.append({"skill_id": record.skill_id, "reason": clean_line(str(reasons[0]))})


def provision(
    org: Org,
    proposal_dir: Path,
    project: Path,
    embedder: Embedder,
    *,
    client: Any = None,
    model: str = "",
    discovery: bool = True,
    github: GitHubClient | None = None,
    say: Callable[[str], None] = lambda text: None,
    sleep: Callable[[float], None] = time.sleep,
) -> ProvisionResult:
    dest = proposal_dir / "skills"
    lock = Lockfile.load(proposal_dir / "skills.lock")
    project_lock = Lockfile.load(project / "skills.lock")
    decisions = Decisions(project / "skills" / "decisions.yaml")
    catalog = SkillCatalog(proposal_dir / "catalog.sqlite", embedder)
    result = ProvisionResult()
    try:
        catalog.rebuild(_all_records(project_lock, project, lock, dest, decisions))
        if discovery:
            needs = needs_without_a_skill(org, catalog, embedder)
            if needs:
                ordered = needs_in_search_order(org, needs)
                result.discovery += discover_skills(ordered, lock, dest, client=github, say=say, sleep=sleep)
                lock.save()
                catalog.rebuild(_all_records(project_lock, project, lock, dest, decisions))
        else:
            result.discovery.append("Skill discovery was turned off.")

        records = _all_records(project_lock, project, lock, dest, decisions)
        _flagged(org, lock, records, result)
        eligible = catalog.eligible()
        vectors = {record.skill_id: vector for record, vector in eligible}
        lists = shortlist(org, eligible, embedder, decisions)

        verdicts: dict[tuple[str, str], Verdict] = {}
        report = RerankReport()
        if client is not None and any(lists.values()):
            say("Asking the model which shortlisted skills really fit each role.")
            cache = RerankCache(proposal_dir / "rerank.sqlite")
            verdicts, report = rerank(org, lists, client, model, cache)
            cache.close()
        result.rerank = {
            "tokens": report.total_tokens,
            "input_tokens": report.input_tokens,
            "output_tokens": report.output_tokens,
            "cached_input_tokens": report.cached_input_tokens,
            "calls": report.calls,
            "judged": report.judged,
            "reused": report.reused,
            "failed_roles": report.failed_roles,
            "chose_none": report.chose_none,
            "raw_log": report.raw_log,
            "used_model": client is not None,
        }

        for ref, role in sorted(org.roles.items()):
            judged_by_model = client is not None and ref not in report.failed_roles
            chosen: list[tuple[Candidate, Verdict | None]] = []
            for candidate in lists[ref]:
                verdict = verdicts.get((ref, candidate.record.skill_id))
                if judged_by_model:
                    if verdict is not None and verdict.fit:
                        chosen.append((candidate, verdict))
                elif candidate.similarity >= MATCH_THRESHOLD:
                    chosen.append((candidate, None))
            chosen = _unique(chosen, vectors)[: role.max_skills]
            result.matches[ref] = [
                _to_match(candidate, verdict, role.allowed_skill_categories, decisions) for candidate, verdict in chosen
            ]
            missing = []
            for need in role.skill_needs:
                query = embedder.embed_query(need)
                best = max((float(query @ vectors[c.record.skill_id]) for c, _ in chosen), default=0.0)
                if best < GAP_SIMILARITY:
                    missing.append(need)
            if missing:
                result.gaps[ref] = missing
    finally:
        catalog.close()
    return result
