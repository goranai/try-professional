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


def test_a_managers_answer_in_its_own_words() -> None:
    """A real tech lead's answer to an escalation was refused twice: "department" for an id, and one criterion as text."""
    answer = {
        "decision": "amend_plan",
        "guidance": "Shrink it to one defect.",
        "activate_departments": [{"department": "research", "why": "check it isn't reported"}],
        "added_work_packages": [
            {
                "title": "Check one README link",
                "department": "engineering",
                "acceptance": "The link is shown to be broken on main",
            }
        ],
    }
    parsed = parse_payload("EscalationResolution", settle_payload("EscalationResolution", answer))
    assert parsed.activate_departments[0].id == "research"  # type: ignore[attr-defined]
    package = parsed.added_work_packages[0]  # type: ignore[attr-defined]
    assert (package.id, package.owner_department) == ("check-one-readme-link", "engineering")
    assert package.acceptance == ["The link is shown to be broken on main"]


def test_more_words_from_a_real_repository_job() -> None:
    """Each of these refused finished work in one real run on step-5-preview."""
    change = {
        "summary": "Found a typo on main",
        "tests": [{"cmd": "grep -n 'you platform' README.md", "passed": True}],
        "notes": {"did": "Checked the README"},
    }
    parsed = parse_payload("CodeChange", settle_payload("CodeChange", change))
    assert parsed.tests[0].command == "grep -n 'you platform' README.md"  # type: ignore[attr-defined]
    assert parsed.tests[0].result == "passed"  # type: ignore[attr-defined]
    verdict = {"gate": "qa", "subject_artifact_id": "a", "decision": "changes_requested",
               "issues": [{"severity": "major", "summary": "No issue URL yet"}], "notes": {"did": "Re-checked"}}  # fmt: skip
    assert parse_payload("ReviewVerdict", settle_payload("ReviewVerdict", verdict)).issues[0].description == (  # type: ignore[attr-defined]
        "No issue URL yet"
    )


def test_notes_in_other_words_keep_the_work() -> None:
    from try_professional.core.artifacts import settle_notes

    _, notes = settle_notes(
        {}, {"did": "Read the README", "did_not_do": "Post the issue", "lessons": "Check main", "x": 1}
    )
    parsed = parse_payload("WorkNotes", notes)
    assert parsed.did == "Read the README Not done: Post the issue"  # type: ignore[attr-defined]
    assert parsed.lesson == "Check main"  # type: ignore[attr-defined]


def test_a_payload_sent_as_text_and_met_in_other_words() -> None:
    """One model sent its whole payload as JSON text three times, and then wrote whether a criterion was met its way."""
    import json

    payload = _content(done_criteria_met=[{"criterion": "Five posts", "status": "met"},
                                          {"criterion": "A caption each", "achieved": "yes"},
                                          {"criterion": "Posting times", "status": "partial"}])  # fmt: skip
    parsed = parse_payload("PublicContent", settle_payload("PublicContent", json.dumps(payload)))
    assert [c.met for c in parsed.done_criteria_met] == [True, True, False]  # type: ignore[attr-defined]
    with pytest.raises(ArtifactError):
        parse_payload("PublicContent", settle_payload("PublicContent", "not json at all"))
    unclear = _content(done_criteria_met=[{"criterion": "Five posts", "status": "mostly"}])
    with pytest.raises(ArtifactError, match="met"):
        parse_payload("PublicContent", settle_payload("PublicContent", unclear))


def test_a_task_without_an_id_and_a_review_in_its_own_words() -> None:
    """From one real multi-department job: three refusals over wording, none over content."""
    plan = {"tasks": [{"title": "Draft the caption", "owner_role": "marketing/social", "goal": "Write it",
                       "expected_output": "A caption", "definition_of_done": ["One caption"],
                       "output_type": "PublicContent"}]}  # fmt: skip
    settled = settle_payload("TaskPlan", plan)
    assert settled["tasks"][0]["id"] == "draft-the-caption"
    verdict = {"gate": "qa", "subject_artifact_id": "a", "decision": "changes_requested",
               "summary": "Two problems", "checks_performed": ["read it"],
               "issues": [{"severity": "nit", "description": "A typo"}, {"severity": "critical", "description": "Wrong price"}]}  # fmt: skip
    parsed = parse_payload("ReviewVerdict", settle_payload("ReviewVerdict", verdict))
    assert [i.severity for i in parsed.issues] == ["minor", "blocker"]  # type: ignore[attr-defined]


def test_a_large_jobs_wording() -> None:
    """From a real three-department job on step-5-preview, each of which refused work."""
    from try_professional.core.artifacts import settle_flags

    tasks = {"work_package_id": "wp1", "tasks": [
        {"id": "t1", "owner_role": "marketing/social", "output_type": "PublicContent",
         "brief": {"title": "Write the post", "goal": "One post", "expected_output": "A post",
                   "definition_of_done": ["One post"]}},
        {"owner_role": "marketing/social", "output_type": "PublicContent", "goal": "Pick the time. Use the research.",
         "expected_output": "A time", "definition_of_done": ["A time"], "memory": [], "skills": []},
    ]}  # fmt: skip
    parsed = parse_payload("TaskPlan", settle_payload("TaskPlan", tasks))
    assert [(t.id, t.title) for t in parsed.tasks] == [("t1", "Write the post"), ("pick-the-time", "Pick the time")]  # type: ignore[attr-defined]
    answer = {"decision": "amend_plan", "activate_departments": [{"id": "research/research-lead", "reason": "facts"}],
              "added_work_packages": [{"id": "p2", "title": "Find what people like", "department": "research",
                                       "description": "Five reasons people like sourdough, with sources"}]}  # fmt: skip
    resolved = parse_payload("EscalationResolution", settle_payload("EscalationResolution", answer))
    assert resolved.activate_departments[0].id == "research"  # type: ignore[attr-defined]
    assert resolved.added_work_packages[0].acceptance == [  # type: ignore[attr-defined]
        "Done as described: Five reasons people like sourdough, with sources"
    ]
    finding = {"statement": "People like the tang", "sources": ["https://x.example"], "confidence": "moderate"}
    assert (
        settle_payload("ResearchReport", {"question": "q", "findings": [finding, {**finding, "confidence": 0.9}]})[
            "findings"
        ][1]["confidence"]
        == "high"
    )
    payload, flags = settle_flags(_content(flags={"public_facing": True}), {"touches_license": False})
    assert "flags" not in payload and flags == {"touches_license": False, "public_facing": True}, "moved, not lost"


def test_a_reviews_commentary_and_an_activation_of_nothing() -> None:
    """From repeated real runs: reviewers added their own fields, and a CEO asked to activate no department."""
    verdict = {"gate": "qa", "subject_artifact_id": "a", "decision": "approve", "checked": ["wc -w"],
               "uncertainty": "none", "decision_rationale": "fine", "not_verified": [],
               "issues": [{"severity": "note", "description": "Verified the word count independently"}]}  # fmt: skip
    parsed = parse_payload("ReviewVerdict", settle_payload("ReviewVerdict", verdict))
    assert parsed.decision == "approve" and parsed.issues[0].severity == "minor"  # type: ignore[attr-defined]
    answer = {"decision": "resume", "guidance": "Resubmit the review",
              "activate_departments": [{"reason": "Only Marketing must act"}]}  # fmt: skip
    assert (
        parse_payload("EscalationResolution", settle_payload("EscalationResolution", answer)).activate_departments == []
    )  # type: ignore[attr-defined]


def test_flags_in_the_wrong_place_are_still_refused() -> None:
    """Flags decide which reviews run, so they're never quietly dropped."""
    with pytest.raises(ArtifactError, match="flags"):
        parse_payload("PublicContent", settle_payload("PublicContent", _content(flags={"public_facing": True})))


def test_a_wrong_top_level_field_is_still_refused() -> None:
    """At the top, an unknown field usually means the wrong type of hand-in altogether."""
    with pytest.raises(ArtifactError, match="title"):
        parse_payload("PublicContent", settle_payload("PublicContent", _content(title="Week one")))
