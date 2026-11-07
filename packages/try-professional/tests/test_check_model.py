"""check-model is judged by code, so a fake model can show every verdict with no cost."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from try_professional.adapters.api_runner.clients import ChatRequest, ChatResponse, ClientError, ToolCall, ToolResult
from try_professional.check_model import CAVEATS, NOT_RECOMMENDED, SUITABLE, ModelUnreachable, check_model, render_check
from try_professional.cli import main

GOOD_NOTE = {
    "audience": "local customers",
    "channel": "web",
    "locale": "en",
    "body": "Our sourdough is baked fresh every morning. Come and try a warm loaf.",
    "done_criteria_met": [
        {"criterion": "The note mentions sourdough", "met": True},
        {"criterion": "The note is at most two sentences", "met": True},
        {"criterion": "The note gives no price", "met": True},
    ],
}
GOOD_REPORT = {
    "question": "What temperature does water boil at?",
    "findings": [{"statement": "Water boils at 100 C at sea level", "confidence": "high", "sources": ["textbook"]}],
}
REPLIES = {
    "kopi": "Kopi pagi adalah awal yang baik untuk hari ini.",
    "コーヒー": "朝のコーヒーは一日の始まりです。",
    "قهوة": "قهوة الصباح تبدأ يومي بنشاط.",
}


def _last_prompt(messages: list[Any]) -> str:
    return next(m["content"] for m in reversed(messages) if m["role"] == "user" and isinstance(m["content"], str))


class Fake:
    """A model that does everything right, unless a test changes one answer."""

    def __init__(self, **changes: Any) -> None:
        self.changes = changes
        self.calls = 0
        self.seen: list[str] = []

    def user_message(self, text: str) -> Any:
        return {"role": "user", "content": text}

    def tool_results_messages(self, results: list[ToolResult]) -> list[Any]:
        return [{"role": "tool", "content": r.content} for r in results]

    def _good(self, prompt: str) -> tuple[str, dict[str, Any]]:
        if "record_number with the number" in prompt:
            return "record_number", {"number": 4821}
        if "ResearchReport" in prompt:
            return "submit_artifact", {"payload": GOOD_REPORT}
        if "PublicContent" in prompt:
            return "submit_artifact", {"payload": GOOD_NOTE}
        if "Summarize" in prompt:
            return "submit_summary", {"summary": "The delivery was late."}
        for word, text in REPLIES.items():
            if word in prompt:
                return "submit_reply", {"text": text}
        raise AssertionError(prompt)

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.calls += 1
        prompt = next(m["content"] for m in request.messages if m["role"] == "user")
        self.seen.append(prompt)
        name, arguments = self._good(prompt)
        change = next((value for key, value in self.changes.items() if key in prompt), None)
        retrying = sum(1 for m in request.messages if m["role"] == "user") > 1
        call: ToolCall | None = ToolCall(id=f"c{self.calls}", name=name, arguments=arguments)
        if change is not None and not (retrying and self.changes.get("recovers")):
            call = change if isinstance(change, ToolCall) or change is None else None
        return ChatResponse(
            text="",
            tool_calls=[call] if call else [],
            stop_reason="tool_use",
            assistant_message={"role": "assistant", "content": ""},
            input_tokens=100,
            output_tokens=10,
            cached_input_tokens=40,
            model="fake",
        )


def _bad_call(name: str = "submit_artifact", **arguments: Any) -> ToolCall:
    return ToolCall(id="bad", name=name, arguments=arguments)


class TestVerdicts:
    def test_a_good_model_is_suitable(self) -> None:
        report = check_model(Fake(), "m")
        assert report.verdict == SUITABLE, report.caveats
        assert all(c.passed and c.first_try for c in report.checks)

    def test_no_tool_calling_means_not_recommended(self) -> None:
        report = check_model(Fake(**{"record_number with the number": "text"}), "m")
        assert report.verdict == NOT_RECOMMENDED

    def test_a_model_that_cant_fill_a_structure_isnt_recommended(self) -> None:
        bad = _bad_call(payload={"question": "q", "made_up_field": 1})
        assert check_model(Fake(ResearchReport=bad), "m").verdict == NOT_RECOMMENDED

    def test_needing_a_retry_is_a_caveat(self) -> None:
        bad = _bad_call(payload={"question": "q", "made_up_field": 1})
        report = check_model(Fake(ResearchReport=bad, recovers=True), "m")
        assert report.verdict == CAVEATS
        assert any("retry" in line for line in report.caveats)

    def test_ignoring_a_brief_is_a_caveat_and_is_named(self) -> None:
        note = {**GOOD_NOTE, "done_criteria_met": []}
        report = check_model(Fake(PublicContent=_bad_call(payload=note)), "m")
        assert report.verdict == CAVEATS
        assert any("Following a brief" in line for line in report.caveats)

    @pytest.mark.parametrize(
        "body",
        [
            "Our bread is baked fresh every morning.",
            "Our sourdough costs $5.",
            "Our sourdough is fresh. It is warm. It is good.",
        ],
        ids=["left out what it was told to mention", "gave a price", "too long"],
    )
    def test_each_part_of_a_brief_is_checked_by_code(self, body: str) -> None:
        report = check_model(Fake(PublicContent=_bad_call(payload={**GOOD_NOTE, "body": body})), "m")
        brief = next(c for c in report.checks if c.name == "Following a brief")
        assert not brief.passed

    def test_replying_in_the_wrong_language_is_a_caveat(self) -> None:
        wrong = _bad_call("submit_reply", text="Morning coffee is the start of the day.")
        report = check_model(Fake(kopi=wrong), "m")
        assert report.verdict == CAVEATS
        assert any("Indonesian" in line for line in report.caveats)

    def test_following_text_inside_data_is_a_caveat(self) -> None:
        obeyed = _bad_call("record_number", number=999)
        report = check_model(Fake(Summarize=obeyed), "m")
        assert report.verdict == CAVEATS
        assert any("untrusted" in line for line in report.caveats)

    def test_broken_json_fails_its_check(self) -> None:
        broken = ToolCall(id="b", name="record_number", arguments={}, parse_error="not JSON")
        assert check_model(Fake(**{"record_number with the number": broken}), "m").verdict == NOT_RECOMMENDED


class TestWhatItReports:
    def test_tokens_are_split(self) -> None:
        report = check_model(Fake(), "m")
        assert report.calls == 7
        assert (report.input_tokens, report.cached_input_tokens, report.output_tokens) == (700, 280, 70)

    def test_it_says_what_it_doesnt_measure(self) -> None:
        assert "doesn't measure the quality" in render_check(check_model(Fake(), "m"))

    def test_an_outage_gives_no_verdict(self) -> None:
        class Down(Fake):
            def complete(self, request: ChatRequest) -> ChatResponse:
                raise ClientError("HTTP 502", retryable=True)

        with pytest.raises(ModelUnreachable):
            check_model(Down(), "m")


class TestCommand:
    def test_dry_run_needs_no_key_and_spends_nothing(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        assert main(["check-model", "--dry-run"]) == 0
        output = capsys.readouterr().out
        assert "Verdict: suitable" in output and "scripted" in output

    def test_no_model_set_says_how_to_set_one(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        for name in ("TRYPRO_LLM_BASE_URL", "TRYPRO_LLM_MODEL"):
            monkeypatch.delenv(name, raising=False)
        assert main(["check-model"]) == 2
        assert "TRYPRO_LLM_BASE_URL" in capsys.readouterr().out
