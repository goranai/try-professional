"""Model clients, checked at the HTTP level with no network and no cost.

The Anthropic tests run the real SDK against a local stub, so they see what the SDK
actually sends. They prove the request shape, not behavior of the live API.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx
import pytest

from try_professional.adapters.api_runner.clients import (
    ANTHROPIC_FALLBACK_BETA,
    AnthropicClient,
    ChatRequest,
    ClientError,
    OpenAICompatibleClient,
    ToolResult,
    ToolSpec,
)

TOOL = ToolSpec("submit_artifact", "Submit", {"type": "object", "properties": {"payload": {"type": "object"}}})


def _request(model: str = "test-model") -> ChatRequest:
    return ChatRequest(system="Be careful.", messages=[{"role": "user", "content": "Halo"}], tools=[TOOL], model=model)


class TestOpenAICompatible:
    def test_a_requested_temperature_is_sent(self) -> None:
        sent: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(json.loads(request.content))
            return httpx.Response(200, json=self._ok({"role": "assistant", "content": "ok"}))

        client = self._client(handler)
        client.complete(_request())
        request = _request()
        request.temperature = 0.0
        client.complete(request)
        assert "temperature" not in sent[0]
        assert sent[1]["temperature"] == 0.0

    def test_a_model_that_refuses_a_temperature_still_answers(self) -> None:
        sent: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            sent.append(body)
            if "temperature" in body:
                return httpx.Response(400, text="Unsupported value: 'temperature'")
            return httpx.Response(200, json=self._ok({"role": "assistant", "content": "ok"}))

        client = self._client(handler)
        request = _request()
        request.temperature = 0.0
        assert client.complete(request).text == "ok"
        assert client.complete(request).text == "ok"
        assert ["temperature" in body for body in sent] == [True, False, False]

    def test_other_bad_requests_still_fail(self) -> None:
        client = self._client(lambda request: httpx.Response(400, text="bad model"))
        request = _request()
        request.temperature = 0.0
        with pytest.raises(ClientError):
            client.complete(request)

    def _client(self, handler: Any, **kwargs: Any) -> OpenAICompatibleClient:
        return OpenAICompatibleClient(
            base_url="https://api.example.com/v1",
            transport=httpx.MockTransport(handler),
            sleep=lambda _: None,
            **kwargs,
        )

    def _ok(self, message: dict[str, Any], finish: str = "stop") -> dict[str, Any]:
        return {
            "model": "served-model",
            "choices": [{"message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        }

    def test_request_and_response_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_KEY", "secret-value")
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers["authorization"]
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json=self._ok(
                    {
                        "content": None,
                        "tool_calls": [
                            {"id": "c1", "function": {"name": "submit_artifact", "arguments": '{"payload": {"a": 1}}'}}
                        ],
                    },
                    "tool_calls",
                ),
            )

        response = self._client(handler, api_key_env="TEST_KEY").complete(_request())
        assert seen["url"] == "https://api.example.com/v1/chat/completions"
        assert seen["auth"] == "Bearer secret-value"
        assert seen["body"]["messages"][0] == {"role": "system", "content": "Be careful."}
        assert seen["body"]["tool_choice"] == "auto"
        assert seen["body"]["tools"][0]["function"]["name"] == "submit_artifact"
        assert seen["body"]["max_tokens"] == 16000
        assert response.tool_calls[0].arguments == {"payload": {"a": 1}}
        assert (response.stop_reason, response.input_tokens, response.output_tokens) == ("tool_use", 11, 7)
        assert response.model == "served-model"

    @pytest.mark.parametrize(
        "usage",
        [{"prompt_tokens_details": {"cached_tokens": 40}}, {"prompt_cache_hit_tokens": 40}],
    )
    def test_cached_tokens_are_read_from_either_format(self, usage: dict[str, Any]) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = self._ok({"content": "hi"})
            body["usage"].update(usage)
            return httpx.Response(200, json=body)

        assert self._client(handler).complete(_request()).cached_input_tokens == 40

    def test_max_tokens_parameter_is_configurable(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=self._ok({"content": "hi"}))

        self._client(handler, max_tokens_param="max_completion_tokens").complete(_request())
        assert "max_completion_tokens" in seen and "max_tokens" not in seen

    def test_bad_tool_json_is_reported_not_raised(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=self._ok(
                    {"tool_calls": [{"id": "c", "function": {"name": "x", "arguments": "{broken"}}]}, "tool_calls"
                ),
            )

        call = self._client(handler).complete(_request()).tool_calls[0]
        assert call.parse_error and "not valid JSON" in call.parse_error

    def test_rate_limit_is_retried(self) -> None:
        attempts = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) == 1:
                return httpx.Response(429, text="slow down")
            return httpx.Response(200, json=self._ok({"content": "hi"}))

        assert self._client(handler).complete(_request()).text == "hi"
        assert len(attempts) == 2

    def test_retries_are_bounded(self) -> None:
        attempts = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(503, text="down")

        with pytest.raises(ClientError) as caught:
            self._client(handler, max_retries=2).complete(_request())
        assert caught.value.retryable and len(attempts) == 3

    def test_gateway_html_is_not_copied_into_errors(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="<html>huge page</html>", headers={"content-type": "text/html"})

        with pytest.raises(ClientError) as caught:
            self._client(handler, max_retries=0).complete(_request())
        assert "huge page" not in str(caught.value) and "502" in str(caught.value)

    def test_bad_request_is_not_retried(self) -> None:
        attempts = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(400, text="bad model")

        with pytest.raises(ClientError) as caught:
            self._client(handler).complete(_request())
        assert not caught.value.retryable and len(attempts) == 1

    def test_content_filter_is_a_refusal(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=self._ok({"content": ""}, "content_filter"))

        assert self._client(handler).complete(_request()).stop_reason == "refusal"

    def test_missing_key_fails_clearly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
        with pytest.raises(ClientError, match="NOT_SET_ANYWHERE"):
            OpenAICompatibleClient(base_url="https://x", api_key_env="NOT_SET_ANYWHERE")

    def test_tool_results_become_tool_messages(self) -> None:
        client = OpenAICompatibleClient(base_url="https://x")
        messages = client.tool_results_messages([ToolResult("c1", "ok"), ToolResult("c2", "no", is_error=True)])
        assert [m["tool_call_id"] for m in messages] == ["c1", "c2"]


class _Stub:
    """A minimal Messages API that records requests and plays back responses."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.responses: list[tuple[int, dict[str, Any]]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("content-length", 0))
                stub.requests.append(
                    {"path": self.path, "headers": dict(self.headers), "body": json.loads(self.rfile.read(length))}
                )
                status, body = stub.responses.pop(0)
                encoded = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *args: Any) -> None:
                return

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _message(content: list[dict[str, Any]], stop_reason: str = "end_turn", **extra: Any) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 21, "output_tokens": 9},
        **extra,
    }


class TestAnthropic:
    @pytest.fixture
    def stub(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Stub]:
        pytest.importorskip("anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        server = _Stub()
        yield server
        server.close()

    def test_request_shape(self, stub: _Stub) -> None:
        stub.responses.append(
            (
                200,
                _message(
                    [{"type": "tool_use", "id": "t1", "name": "submit_artifact", "input": {"payload": {}}}], "tool_use"
                ),
            )
        )
        response = AnthropicClient(base_url=stub.url).complete(_request("claude-opus-5-5"))

        sent = stub.requests[0]
        body = sent["body"]
        assert sent["path"].startswith("/v1/messages")
        assert ANTHROPIC_FALLBACK_BETA in sent["headers"].get(
            "anthropic-beta", sent["headers"].get("Anthropic-Beta", "")
        )
        assert body["fallbacks"] == "default"
        assert body["output_config"] == {"effort": "high"}
        assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert body["tools"][0]["name"] == "submit_artifact"
        assert body["max_tokens"] == 16000
        # These fail with a 400 on current models, so they must never be sent.
        for forbidden in ("temperature", "top_p", "top_k", "tool_choice", "thinking"):
            assert forbidden not in body

        assert response.stop_reason == "tool_use"
        assert response.tool_calls[0].name == "submit_artifact"
        assert (response.input_tokens, response.output_tokens) == (21, 9)
        assert response.model == "claude-opus-5-5"

    def test_haiku_has_no_server_side_fallback(self, stub: _Stub) -> None:
        stub.responses.append((200, _message([{"type": "text", "text": "hi"}])))
        AnthropicClient(base_url=stub.url).complete(_request("claude-haiku-5-5"))
        assert "fallbacks" not in stub.requests[0]["body"]
        assert "beta=true" not in stub.requests[0]["path"]

    def test_fallbacks_can_be_turned_off(self, stub: _Stub) -> None:
        stub.responses.append((200, _message([{"type": "text", "text": "hi"}])))
        AnthropicClient(base_url=stub.url, fallbacks=None).complete(_request("claude-opus-5-5"))
        assert "fallbacks" not in stub.requests[0]["body"]

    def test_thinking_blocks_replay_unchanged(self, stub: _Stub) -> None:
        thinking = {"type": "thinking", "thinking": "", "signature": "sig-abc"}
        tool_use = {"type": "tool_use", "id": "t1", "name": "workspace_list", "input": {}}
        stub.responses.append((200, _message([thinking, tool_use], "tool_use")))
        stub.responses.append((200, _message([{"type": "text", "text": "done"}])))

        client = AnthropicClient(base_url=stub.url)
        request = _request("claude-opus-5-5")
        first = client.complete(request)
        request.messages.append(first.assistant_message)
        request.messages.extend(client.tool_results_messages([ToolResult("t1", "[]")]))
        client.complete(request)

        replayed = stub.requests[1]["body"]["messages"]
        assert replayed[1]["role"] == "assistant"
        assert replayed[1]["content"][0]["type"] == "thinking"
        assert replayed[1]["content"][0]["signature"] == "sig-abc"
        assert replayed[2]["content"] == [
            {"type": "tool_result", "tool_use_id": "t1", "content": "[]", "is_error": False}
        ]

    def test_refusal_is_reported(self, stub: _Stub) -> None:
        stub.responses.append(
            (200, _message([], "refusal", stop_details={"type": "refusal", "category": "cyber", "explanation": "x"}))
        )
        response = AnthropicClient(base_url=stub.url).complete(_request("claude-opus-5-5"))
        assert response.stop_reason == "refusal"
        assert response.detail == "cyber"

    def test_bad_request_is_not_retryable(self, stub: _Stub) -> None:
        stub.responses.append((400, {"type": "error", "error": {"type": "invalid_request_error", "message": "no"}}))
        with pytest.raises(ClientError) as caught:
            AnthropicClient(base_url=stub.url).complete(_request("claude-opus-5-5"))
        assert not caught.value.retryable
