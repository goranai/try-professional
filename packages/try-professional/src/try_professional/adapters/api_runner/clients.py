"""Provider-agnostic model clients.

Anthropic uses the official SDK, an optional extra, because its API surface moves
quickly. Anything OpenAI-compatible uses plain HTTP, so Grok and local servers work
with no extra dependency. Conversation messages are opaque to the runner: each
client appends its provider's own message objects, and replays them unchanged.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

DEFAULT_MAX_TOKENS = 16000
# The most room a reply can be given when one runs out. step-5-preview writes up to 128k; a provider that allows less
# says so, and its own number is used from then on.
MAX_OUTPUT_TOKENS_CEILING = 128_000
OUTPUT_TOKENS_ENV = "TRYPRO_LLM_MAX_OUTPUT_TOKENS"


def output_tokens() -> int:
    """Room for one reply. TRYPRO_LLM_MAX_OUTPUT_TOKENS sets where it starts; a reply that runs out gets more."""
    value = os.environ.get(OUTPUT_TOKENS_ENV, "").strip().replace("_", "")
    return int(value) if value.isdigit() and int(value) > 0 else DEFAULT_MAX_TOKENS


ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Claude Haiku has no server-side fallback, so the parameter is omitted for it.
_NO_SERVER_FALLBACK = ("claude-haiku-",)


class ClientError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        self.retryable = retryable
        super().__init__(message)


def redact(text: str, secret: str | None) -> str:
    """Some providers echo the key in errors, and errors reach the audit log."""
    return text.replace(secret, "***") if secret else text


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    parse_error: str | None = None


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class ChatRequest:
    system: str
    messages: list[Any]
    tools: list[ToolSpec]
    model: str
    max_tokens: int = DEFAULT_MAX_TOKENS
    metadata: dict[str, Any] = field(default_factory=dict)
    # Honoured where the provider allows it. Anthropic's newer models reject it, so that client ignores it.
    temperature: float | None = None


@dataclass
class ChatResponse:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str  # end | tool_use | max_tokens | refusal | other
    assistant_message: Any
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    model: str = ""
    detail: str = ""


class ChatClient(Protocol):
    def user_message(self, text: str) -> Any: ...

    def tool_results_messages(self, results: list[ToolResult]) -> list[Any]: ...

    def complete(self, request: ChatRequest) -> ChatResponse: ...


class AnthropicClient:
    """Claude through the official SDK."""

    def __init__(
        self,
        *,
        api_key_env: str | None = "ANTHROPIC_API_KEY",
        effort: str | None = "high",
        fallbacks: str | None = "default",
        base_url: str | None = None,
    ) -> None:
        try:
            import anthropic
        except ImportError as error:
            raise ClientError(
                "provider 'anthropic' needs the SDK: pip install 'try-professional[anthropic]'",
                retryable=False,
            ) from error
        self._sdk = anthropic
        kwargs: dict[str, Any] = {}
        key = os.environ.get(api_key_env) if api_key_env else None
        self._secret = key
        if key:
            kwargs["api_key"] = key
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.Anthropic(**kwargs)
        self.effort = effort
        self.fallbacks = fallbacks

    def user_message(self, text: str) -> Any:
        return {"role": "user", "content": text}

    def tool_results_messages(self, results: list[ToolResult]) -> list[Any]:
        # All results go in one user message, as the API expects for parallel calls.
        return [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content, "is_error": r.is_error}
                    for r in results
                ],
            }
        ]

    def complete(self, request: ChatRequest) -> ChatResponse:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": [{"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}],
            "messages": request.messages,
            "tools": [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in request.tools
            ],
        }
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        use_fallbacks = self.fallbacks and not request.model.startswith(_NO_SERVER_FALLBACK)
        try:
            if use_fallbacks:
                kwargs["fallbacks"] = self.fallbacks
                response = self._client.beta.messages.create(betas=[ANTHROPIC_FALLBACK_BETA], **kwargs)
            else:
                response = self._client.messages.create(**kwargs)
        except self._sdk.BadRequestError as error:
            raise ClientError(
                redact(f"Anthropic rejected the request: {error}", self._secret), retryable=False
            ) from error
        except self._sdk.AuthenticationError as error:
            raise ClientError(
                redact(f"Anthropic authentication failed: {error}", self._secret), retryable=False
            ) from error
        except self._sdk.PermissionDeniedError as error:
            raise ClientError(
                redact(f"Anthropic denied the request: {error}", self._secret), retryable=False
            ) from error
        except self._sdk.NotFoundError as error:
            raise ClientError(
                redact(f"Anthropic could not find the model: {error}", self._secret), retryable=False
            ) from error
        except self._sdk.APIError as error:
            raise ClientError(redact(f"Anthropic request failed: {error}", self._secret), retryable=True) from error

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                # A strict schema isn't requested, so the runner validates every input itself.
                arguments = block.input if isinstance(block.input, dict) else {}
                calls.append(ToolCall(id=block.id, name=block.name, arguments=arguments))

        stop = {"end_turn": "end", "tool_use": "tool_use", "max_tokens": "max_tokens", "refusal": "refusal"}
        stop_details = getattr(response, "stop_details", None)
        return ChatResponse(
            text="".join(text_parts),
            tool_calls=calls,
            stop_reason=stop.get(response.stop_reason or "", "other"),
            # The whole content list goes back unchanged, which keeps thinking blocks valid.
            assistant_message={"role": "assistant", "content": response.content},
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cached_input_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            model=response.model,
            detail=str(getattr(stop_details, "category", "") or "") if response.stop_reason == "refusal" else "",
        )


def _cached_tokens(usage: dict[str, Any]) -> int:
    """Providers report prompt caching differently, and many report nothing."""
    details = usage.get("prompt_tokens_details") or {}
    return int(details.get("cached_tokens") or usage.get("prompt_cache_hit_tokens") or 0)


_VAGUE = '"message":"invalidrequest."'


def _vague_refusal(response: httpx.Response) -> bool:
    """A 400 with no reason at all. Gateways send it when an upstream provider fails, and the same request often
    works a moment later. A 400 that says what's wrong is a real refusal, and isn't retried."""
    if response.status_code != 400:
        return False
    text = response.text.lower()
    return "unknown_provider_failure" in text or _VAGUE in text.replace(" ", "")


def _vague_error(error: ClientError) -> bool:
    detail = str(error).lower()
    return "http 400" in detail and ("unknown_provider_failure" in detail or _VAGUE in detail.replace(" ", ""))


def _plain(text: str) -> str:
    """Markdown headings as plain "Heading:" lines. Nothing else changes."""
    return re.sub(r"(?m)^#{1,6} +(.+?)\s*$", r"\1:", text)


class OpenAICompatibleClient:
    """Any /chat/completions API, using plain HTTP."""

    RETRYABLE = frozenset({408, 409, 429, 500, 502, 503, 504})

    def __init__(
        self,
        *,
        base_url: str,
        api_key_env: str | None = None,
        max_tokens_param: str = "max_tokens",
        max_retries: int = 5,
        timeout: float = 600.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        headers = {}
        self._secret: str | None = None
        if api_key_env:
            key = os.environ.get(api_key_env)
            if not key:
                raise ClientError(f"environment variable {api_key_env} is not set", retryable=False)
            headers["Authorization"] = f"Bearer {key}"
            self._secret = key
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + "/", headers=headers, timeout=timeout, transport=transport
        )
        self.max_tokens_param = max_tokens_param
        # Lowered if the provider says a model allows less, and kept for later calls.
        self.output_ceiling = MAX_OUTPUT_TOKENS_CEILING
        self._temperature_refused = False
        # Some gateways refuse a long system message beside some user text, and take the same words in one message.
        self._fold_system = False
        self._plain_headings = False
        self.max_retries = max_retries
        self._sleep = sleep

    def user_message(self, text: str) -> Any:
        return {"role": "user", "content": text}

    def tool_results_messages(self, results: list[ToolResult]) -> list[Any]:
        return [{"role": "tool", "tool_call_id": r.call_id, "content": r.content} for r in results]

    def _messages(self, request: ChatRequest) -> list[Any]:
        shaped = self._shape(request)
        if not self._plain_headings:
            return shaped
        return [{**m, "content": _plain(m["content"])} if isinstance(m.get("content"), str) else m for m in shaped]

    def _shape(self, request: ChatRequest) -> list[Any]:
        messages = list(request.messages)
        if not self._fold_system:
            return [{"role": "system", "content": request.system}, *messages]
        if messages and messages[0].get("role") == "user" and isinstance(messages[0].get("content"), str):
            first = {**messages[0], "content": f"{request.system}\n\n{messages[0]['content']}"}
            return [first, *messages[1:]]
        return [{"role": "user", "content": request.system}, *messages]

    def _reshape(self, request: ChatRequest, body: dict[str, Any], refused: ClientError) -> dict[str, Any]:
        """The same content, in shapes a gateway that refuses with no reason has accepted: first the instructions at
        the top of the first message, then plain headings instead of markdown ones. step-5-preview's gateway refused
        a real assignment with "## " headings, every time, and took it word for word with "Heading:" lines. A shape
        that works is kept for the rest of this client's calls."""
        for flag in ("_fold_system", "_plain_headings"):
            if getattr(self, flag):
                continue
            setattr(self, flag, True)
            body["messages"] = self._messages(request)
            try:
                return self._post(body)
            except ClientError as again:
                if not _vague_error(again):
                    raise
                refused = again
        raise refused

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self.max_retries + 1):
            last = attempt == self.max_retries
            try:
                response = self._http.post("chat/completions", json=body)
            except httpx.HTTPError as error:
                if last:
                    raise ClientError(redact(f"request failed: {error}", self._secret), retryable=True) from error
                self._sleep(min(30, 2 ** (attempt + 1)))
                continue
            # A vague 400 gets one more try. If it comes back, complete() changes the request's shape instead.
            vague = _vague_refusal(response) and attempt == 0
            if (response.status_code in self.RETRYABLE or vague) and not last:
                self._sleep(min(30, 2 ** (attempt + 1)))
                continue
            if response.status_code >= 400:
                retryable = response.status_code in self.RETRYABLE
                # Gateways often return a whole HTML page, which is only noise in the audit log.
                is_html = "html" in response.headers.get("content-type", "").lower()
                detail = "gateway error page" if is_html else redact(response.text[:500], self._secret)
                raise ClientError(f"HTTP {response.status_code}: {detail}", retryable=retryable)
            parsed: dict[str, Any] = response.json()
            return parsed
        raise AssertionError("unreachable")

    def complete(self, request: ChatRequest) -> ChatResponse:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": self._messages(request),
            self.max_tokens_param: min(request.max_tokens, self.output_ceiling),
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.input_schema},
                }
                for t in request.tools
            ]
            body["tool_choice"] = "auto"
        if request.temperature is not None and not self._temperature_refused:
            body["temperature"] = request.temperature
        try:
            data = self._post(body)
        except ClientError as refused:
            detail = str(refused).lower()
            if _vague_error(refused) and not (self._fold_system and self._plain_headings):
                data = self._reshape(request, body, refused)
            elif "http 400" in detail and self.max_tokens_param.replace("_", " ") in detail.replace("_", " "):
                # Asked for more room than this model has. Its own maximum, if it says one, and half if it doesn't.
                asked = body[self.max_tokens_param]
                allowed = [int(n) for n in re.findall(r"\b(\d{4,7})\b", detail) if 1024 <= int(n) < asked]
                body[self.max_tokens_param] = max(allowed) if allowed else max(1024, body[self.max_tokens_param] // 2)
                self.output_ceiling = body[self.max_tokens_param]
                data = self._post(body)
            elif "temperature" in body and "http 400" in detail and "temperature" in detail:
                # Some models only accept their default. That's not worth failing the call.
                self._temperature_refused = True
                del body["temperature"]
                data = self._post(body)
            else:
                raise
        choices = data.get("choices") or []
        if not choices:
            raise ClientError(f"response has no choices: {str(data)[:300]}", retryable=True)
        choice = choices[0]
        message = choice.get("message") or {}
        calls = []
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            raw = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw) if isinstance(raw, str) else raw
                error = None if isinstance(arguments, dict) else "arguments must be a JSON object"
            except json.JSONDecodeError as parse_error:
                arguments, error = {}, f"arguments are not valid JSON: {parse_error}"
            calls.append(
                ToolCall(
                    id=item.get("id", ""), name=function.get("name", ""), arguments=arguments or {}, parse_error=error
                )
            )
        stop = {"stop": "end", "tool_calls": "tool_use", "length": "max_tokens", "content_filter": "refusal"}
        usage = data.get("usage") or {}
        return ChatResponse(
            text=message.get("content") or "",
            tool_calls=calls,
            stop_reason=stop.get(choice.get("finish_reason") or "", "other"),
            assistant_message=message,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            cached_input_tokens=_cached_tokens(usage),
            model=data.get("model", request.model),
        )
