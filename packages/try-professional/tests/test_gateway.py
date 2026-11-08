"""Gateways in front of cheap models fail in their own ways. The client gets past them, and says so when it can't."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from try_professional.adapters.api_runner.clients import ChatRequest, ClientError, OpenAICompatibleClient

VAGUE = {"error": {"type": "invalid_request", "code": "unknown_provider_failure", "message": "Invalid request."}}
OK = {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}], "usage": {}}


def _client(handler: Any, monkeypatch: pytest.MonkeyPatch) -> OpenAICompatibleClient:
    monkeypatch.setenv("TEST_KEY", "test-key-0123456789")
    return OpenAICompatibleClient(
        base_url="https://gateway.example/v1",
        api_key_env="TEST_KEY",
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: None,
    )


def _request() -> ChatRequest:
    return ChatRequest(system="Rules.", messages=[{"role": "user", "content": "Halo"}], tools=[], model="m")


def test_a_vague_refusal_is_retried_with_the_instructions_folded_in(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        messages = json.loads(request.content)["messages"]
        seen.append([m["role"] for m in messages])
        if messages[0]["role"] == "system":
            return httpx.Response(400, json=VAGUE)
        assert messages[0]["content"].startswith("Rules.\n\nHalo")
        return httpx.Response(200, json=OK)

    client = _client(handler, monkeypatch)
    assert client.complete(_request()).text == "hi"
    assert seen[-1] == ["user"] and ["system", "user"] in seen
    client.complete(_request())
    assert seen[-1] == ["user"], "once folded, it stays folded"


def test_then_plain_headings_word_for_word(monkeypatch: pytest.MonkeyPatch) -> None:
    """step-5-preview's gateway refused a real assignment with "## " headings every time, and took it as plain lines."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        text = json.loads(request.content)["messages"][0]["content"]
        seen.append(text)
        return httpx.Response(400, json=VAGUE) if "## " in text else httpx.Response(200, json=OK)

    request = ChatRequest(
        system="## Rules\nBe brief.", messages=[{"role": "user", "content": "## Goal\nSay hi"}], tools=[], model="m"
    )
    client = _client(handler, monkeypatch)
    assert client.complete(request).text == "hi"
    assert seen[-1] == "Rules:\nBe brief.\n\nGoal:\nSay hi"
    calls = len(seen)
    client.complete(request)
    assert len(seen) == calls + 1, "a shape that works is kept"


def test_a_vague_refusal_that_no_shape_gets_past_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=VAGUE)

    with pytest.raises(ClientError, match="Invalid request"):
        _client(handler, monkeypatch).complete(_request())


def test_a_clear_refusal_isnt_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "This model doesn't support tools"}})

    with pytest.raises(ClientError, match="support tools"):
        _client(handler, monkeypatch).complete(_request())
    assert len(calls) == 1


def test_a_model_that_allows_less_room_gets_its_own_maximum(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        wanted = json.loads(request.content)["max_tokens"]
        asked.append(wanted)
        if wanted > 8192:
            return httpx.Response(400, json={"error": {"message": f"max_tokens must be <= 8192, got {wanted}"}})
        return httpx.Response(200, json=OK)

    client = _client(handler, monkeypatch)
    big = ChatRequest(system="s", messages=[{"role": "user", "content": "hi"}], tools=[], model="m", max_tokens=128_000)
    assert client.complete(big).text == "hi"
    assert asked == [128_000, 8192]
    client.complete(big)
    assert asked[-1] == 8192, "kept for later calls"


def test_where_reply_room_starts_can_be_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from try_professional.adapters.api_runner.clients import DEFAULT_MAX_TOKENS, output_tokens

    monkeypatch.delenv("TRYPRO_LLM_MAX_OUTPUT_TOKENS", raising=False)
    assert output_tokens() == DEFAULT_MAX_TOKENS
    monkeypatch.setenv("TRYPRO_LLM_MAX_OUTPUT_TOKENS", "128_000")
    assert output_tokens() == 128_000


def test_a_gateway_that_is_briefly_down_is_waited_out(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = iter([httpx.Response(502, text="bad gateway"), httpx.Response(502, text="bad gateway")])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(replies, httpx.Response(200, json=OK))

    assert _client(handler, monkeypatch).complete(_request()).text == "hi"
