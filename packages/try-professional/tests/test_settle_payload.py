"""Cheap models name fields their own way. That's forgiven, and missing content still isn't."""

from __future__ import annotations

import pytest

from try_professional.core.artifacts import ArtifactError, parse_payload, settle_payload

CONTENT = {
    "audience": "Instagram followers",
    "channel": "Instagram",
    "language": "en",
    "locale": "en",
    "body": "Five posts, with a caption for each.",
}


def _content(**changes: object) -> dict[str, object]:
    return {key: value for key, value in {**CONTENT, **changes}.items() if value is not None}


def test_other_names_for_a_claim_and_a_criterion_are_understood() -> None:
    payload = _content(
        claims=[{"text": "We bake every morning", "status": "unverified", "note": "from the brief"}],
        done_criteria_met=[{"text": "Five posts", "met": True, "why": "listed"}],
    )
    settled = settle_payload("PublicContent", payload)
    assert settled["claims"] == [{"claim": "We bake every morning", "unverified": True}]
    assert settled["done_criteria_met"] == [{"criterion": "Five posts", "met": True}]


def test_a_claim_with_no_source_is_marked_unverified_not_refused() -> None:
    settled = settle_payload("PublicContent", _content(claims=[{"claim": "Best in town"}]))
    assert settled["claims"][0]["unverified"] is True
    sourced = settle_payload("PublicContent", _content(claims=[{"claim": "Open 8 to 5", "url": "https://x.example"}]))
    assert sourced["claims"][0] == {"claim": "Open 8 to 5", "source": "https://x.example"}


def test_a_review_issue_keeps_its_detail() -> None:
    verdict = {
        "gate": "qa",
        "subject_artifact_id": "art-1",
        "decision": "changes_requested",
        "issues": [{"severity": "high", "issue": "Caption 3 is too long", "where": "post 3", "fix": "cut it"}],
    }
    parsed = parse_payload("ReviewVerdict", settle_payload("ReviewVerdict", verdict))
    assert parsed.issues[0].severity == "major"  # type: ignore[attr-defined]
    assert parsed.issues[0].description == "Caption 3 is too long (where: post 3; fix: cut it)"  # type: ignore[attr-defined]


def test_missing_content_is_still_refused() -> None:
    with pytest.raises(ArtifactError, match="body"):
        parse_payload("PublicContent", settle_payload("PublicContent", _content(body=None)))
    with pytest.raises(ArtifactError, match="decision"):
        parse_payload("ReviewVerdict", settle_payload("ReviewVerdict", {"gate": "qa", "subject_artifact_id": "a"}))


def test_a_wrong_top_level_field_is_still_refused() -> None:
    """At the top, an unknown field usually means the wrong type of hand-in altogether."""
    with pytest.raises(ArtifactError, match="title"):
        parse_payload("PublicContent", settle_payload("PublicContent", _content(title="Week one")))
