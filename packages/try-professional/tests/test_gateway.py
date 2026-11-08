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


def test_a_clear_refusal_isnt_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "max_tokens is too large for this model"}})

    with pytest.raises(ClientError, match="max_tokens"):
        _client(handler, monkeypatch).complete(_request())
    assert len(calls) == 1


def test_a_gateway_that_is_briefly_down_is_waited_out(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = iter([httpx.Response(502, text="bad gateway"), httpx.Response(502, text="bad gateway")])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(replies, httpx.Response(200, json=OK))

    assert _client(handler, monkeypatch).complete(_request()).text == "hi"
