"""The one review screen. What it shows is pure text, so it can be tested without a terminal."""

from __future__ import annotations

from .flow import Proposal
from .provision import RoleMatch
from .templates import load_template

SKILL_REQUEST_URL = "https://github.com/goranai/try-professional/issues/new?template=skill_request.yml"
# The line below which a role need counts as a gap is 0.35. A match a little above it is worth a second look.
LOW_CONFIDENCE_SCORE = 0.40
THIN_COVERAGE_PERCENT = 50
_SOURCES = {
    "declared": "declared by its author",
    "proposed": "proposed, your approval assigns it",
    "person": "assigned by a person",
}


def low_confidence(match: RoleMatch) -> bool:
    """A display rule, not a measured one: no model judged it, or it's barely above the line for a gap."""
    return match.judged_by != "model" or match.score < LOW_CONFIDENCE_SCORE


def _match_line(match: RoleMatch) -> list[str]:
    source = _SOURCES.get(match.category_source, match.category_source)
    flag = "  LOW CONFIDENCE: check it fits, or reject it" if low_confidence(match) else ""
    return [
        f"{match.skill_id}  ({match.score:.2f}){flag}",
        f"  {match.reason}. Category {match.category}: {source}.",
    ]


def _estimate_line(bundle: Proposal) -> str:
    estimate = bundle.estimate
    line = (
        f"Estimated per typical run: {estimate.low:,}-{estimate.high:,} tokens "
        f"({estimate.tasks} tasks). An estimate from one measured run, not a measurement."
    )
    if estimate.may_hit_ceiling:
        line += f"\n  This could reach the {estimate.ceiling:,}-token ceiling, which stops a run and asks you."
    return line


def _coverage(bundle: Proposal) -> tuple[int, int]:
    """How many role needs have no skill, and how many there are."""
    total = sum(len(role.skill_needs) for role in bundle.org.roles.values())
    return sum(len(needs) for needs in bundle.provisioned.gaps.values()), total


def _covered_percent(unfilled: int, total: int) -> int:
    return round(100 * (total - unfilled) / total) if total else 0


def _coverage_line(unfilled: int, total: int) -> str:
    filled = total - unfilled
    percent = _covered_percent(unfilled, total)
    line = f"Skill coverage: {filled} of {total} role needs have a skill ({percent}%)."
    if percent < THIN_COVERAGE_PERCENT:
        line += " That's thin. Roles marked GAP start without a skill for that work, and rely on their instructions."
    return line


def render(bundle: Proposal) -> str:
    org = bundle.org
    meta = bundle.meta
    template = load_template(meta["template"])
    match = f", match {meta['template_match']:.2f}" if meta.get("template_match") is not None else ""
    architect = meta["tokens"]["architect"]["total"]
    rerank = meta["tokens"]["rerank"]["total"]
    lines = [
        f"Proposed company: {org.company_name}",
        f"Started from: {template.name}{match}",
    ]
    if architect or rerank:
        lines.append(f"Spent so far: {architect:,} tokens designing, {rerank:,} matching skills.")
    else:
        if meta.get("model") == "the client's model":
            lines.append("Designed by your client's model, which reported no token count to this server.")
        else:
            lines.append("Spent so far: nothing. No model was called, so the template is unchanged.")
    lines.append(_estimate_line(bundle))
    unfilled, total_needs = _coverage(bundle)
    matched = any(bundle.provisioned.matches.values()) or unfilled
    if matched:
        lines.append(_coverage_line(unfilled, total_needs))
        searched = not any("GITHUB_TOKEN" in message for message in bundle.provisioned.discovery)
        if searched and _covered_percent(unfilled, total_needs) < THIN_COVERAGE_PERCENT:
            lines.append(f"  Want more for your industry? Ask for skills here: {SKILL_REQUEST_URL}")
    lines.append("")

    root_department = org.root_role.split("/", 1)[0]
    ordered = [
        org.departments[root_department],
        *[d for d in org.departments.values() if d.id != root_department],
    ]
    total_skills = 0
    for department in ordered:
        lines.append(f"{department.name or department.id}")
        members = [department.head, *[r for r in department.roles if r != department.head]]
        for member in members:
            ref = f"{department.id}/{member}"
            role = org.role(ref)
            marker = f" ({member}, head)" if member == department.head else f" ({member})"
            lines.append(f"  {role.display_name}{marker}: {role.mission}")
            matches = bundle.provisioned.matches.get(ref, [])
            total_skills += len(matches)
            for match_item in matches:
                first, second = _match_line(match_item)
                lines.append(f"    {'?' if low_confidence(match_item) else '+'} {first}")
                lines.append(f"      {second.strip()}")
            gaps = bundle.provisioned.gaps.get(ref, [])
            if gaps:
                lines.append("    GAP: no skill found for " + ", ".join(f'"{need}"' for need in gaps))
        lines.append("")

    for note in bundle.notes:
        lines.append(f"Note: {note}")
    for message in bundle.provisioned.discovery:
        lines.append(message)
    used_model = bundle.provisioned.rerank.get("used_model")
    if not used_model and any(bundle.provisioned.matches.values()):
        lines.append("Skills above were matched by embeddings only, with no model judging fit.")
    for role in bundle.provisioned.rerank.get("failed_roles", []):
        lines.append(f"The model couldn't judge skills for {role}, so embeddings chose them.")

    quarantined = bundle.provisioned.quarantined
    cannot_run = bundle.provisioned.cannot_run
    off_trade = bundle.provisioned.off_trade
    if quarantined or cannot_run or off_trade:
        lines += ["", "Not included in approval:"]
        for item in quarantined:
            lines.append(f"  QUARANTINED  {item['skill_id']}: {item['reason']}")
        for item in cannot_run:
            lines.append(f"  NOT INSTALLED  {item['skill_id']}: {item['reason']}")
        for item in off_trade:
            lines.append(f"  OTHER TRADE  {item['skill_id']}: {item['reason']}")
    unique = {m.skill_id for items in bundle.provisioned.matches.values() for m in items}
    lines += [
        "",
        f"Approving activates {len(org.departments)} departments, {len(org.roles)} roles, "
        f"and {len(unique)} skills in {total_skills} role assignments.",
        "Each role starts with its role card, its skills, and an empty personal memory.",
        "Its roles use this computer: a shell, files, git, and the web. They hold anything that posts, sends, or "
        "opens something outside for your approval, which the API runner enforces. It can't spend money.",
        "When its work shows a role is missing, the CEO proposes one, and you decide: trypro org review",
    ]
    doubtful = sum(1 for items in bundle.provisioned.matches.values() for m in items if low_confidence(m))
    if doubtful:
        lines.append(
            f"{doubtful} of the matches above are marked ? for low confidence. To drop one, choose [e]dit and remove "
            "it, or after approving: trypro skills reject <skill> --role <role> --by <your name>"
        )
    if unfilled:
        lines.append(f"{unfilled} role needs have no skill. Approving saves them, and trypro skills gaps lists them.")
    return "\n".join(lines)
