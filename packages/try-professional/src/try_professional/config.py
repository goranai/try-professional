"""Project configuration (trypro.yaml and .env).

trypro.yaml names environment variables and never holds keys. Values come from the
real environment first, then from the nearest .env file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .adapters.api_runner.clients import AnthropicClient, ChatClient, ClientError, OpenAICompatibleClient
from .org import Org, Role

CONFIG_NAME = "trypro.yaml"
ENV_NAME = ".env"
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

CONFIG_TEMPLATE = """\
# Try Professional project settings. This file never holds keys: it names the
# environment variable that does. Values are read from the environment, then from .env.
org_dir: org
home: .trypro

model:
  provider: openai_compatible
  base_url_env: TRYPRO_LLM_BASE_URL
  api_key_env: TRYPRO_LLM_API_KEY
  model_env: TRYPRO_LLM_MODEL
  # Optional: use a different model per tier. Each one defaults to TRYPRO_LLM_MODEL.
  # tiers:
  #   executive: your-model-name
  #   standard: your-model-name
  #   fast: your-model-name

# Claude through the official SDK. Needs: pip install 'try-professional[anthropic]'
# model:
#   provider: anthropic
#   api_key_env: ANTHROPIC_API_KEY
#   effort: high
#   fallbacks: default          # retry a declined request on another model; null to turn off
#   tiers:
#     standard: claude-opus-5-5

# Embeddings run on this machine, and memory text is never sent to a remote API.
# intfloat/multilingual-e5-large retrieves better but needs far more disk and memory, and
# can't read memory built with another model. See docs/embeddings.md.
memory:
  enabled: true
  embedding_model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
  remote_allowed: false

# Give one department or role its own key, model, or endpoint in org/org.yaml:
#
# llm_overrides:
#   engineering: { model: another-model }
#   engineering/qa: { api_key_env: QA_LLM_KEY }
"""


@dataclass
class ModelConfig:
    provider: str
    tiers: dict[str, str]
    api_key_env: str | None = None
    base_url: str | None = None
    effort: str | None = None
    fallbacks: str | None = None
    max_tokens_param: str = "max_tokens"
    default_model: str | None = None

    def model_for_tier(self, tier: str) -> str:
        model = self.tiers.get(tier) or self.tiers.get("standard") or self.default_model
        if not model:
            raise ClientError(
                "no model is configured. Set TRYPRO_LLM_MODEL in .env, or model.tiers in trypro.yaml.",
                retryable=False,
            )
        return model


@dataclass
class MemoryConfig:
    enabled: bool = True
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    remote_allowed: bool = False


@dataclass
class ProjectConfig:
    root: Path
    org_dir: Path
    home: Path
    model: ModelConfig
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    env_file: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def parse_env_file(text: str) -> dict[str, str]:
    """Parse KEY=value lines. Values are never logged by this or any caller."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.removeprefix("export ").partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[name.strip()] = value
    return values


def load_env_file(start: Path) -> Path | None:
    """Load the nearest .env into the environment. Real environment variables win."""
    for directory in [start, *start.parents]:
        candidate = directory / ENV_NAME
        if candidate.is_file():
            for name, value in parse_env_file(candidate.read_text(encoding="utf-8")).items():
                os.environ.setdefault(name, value)
            return candidate
    return None


def find_config(start: Path) -> Path:
    for directory in [start, *start.parents]:
        candidate = directory / CONFIG_NAME
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"no {CONFIG_NAME} here or above. Run 'trypro init' first.")


def load_config(start: Path) -> ProjectConfig:
    path = find_config(start)
    env_file = load_env_file(path.parent)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    model = raw.get("model") or {}
    memory = raw.get("memory") or {}
    base_url = model.get("base_url")
    if not base_url and model.get("base_url_env"):
        base_url = os.environ.get(model["base_url_env"])
    default_model = os.environ.get(model["model_env"]) if model.get("model_env") else None
    return ProjectConfig(
        root=path.parent,
        org_dir=path.parent / raw.get("org_dir", "org"),
        home=path.parent / raw.get("home", ".trypro"),
        model=ModelConfig(
            provider=model.get("provider", ""),
            tiers=model.get("tiers") or {},
            api_key_env=model.get("api_key_env"),
            base_url=base_url,
            effort=model.get("effort"),
            fallbacks=model.get("fallbacks"),
            max_tokens_param=model.get("max_tokens_param", "max_tokens"),
            default_model=default_model,
        ),
        memory=MemoryConfig(
            enabled=memory.get("enabled", True),
            embedding_model=memory.get("embedding_model", DEFAULT_EMBEDDING_MODEL),
            remote_allowed=memory.get("remote_allowed", False),
        ),
        env_file=env_file,
        raw=raw,
    )


def build_client(config: ModelConfig) -> ChatClient:
    if config.provider == "anthropic":
        return AnthropicClient(
            api_key_env=config.api_key_env,
            effort=config.effort,
            fallbacks=config.fallbacks,
            base_url=config.base_url,
        )
    if config.provider == "openai_compatible":
        if not config.base_url:
            raise ClientError(
                "no model endpoint is configured. Set TRYPRO_LLM_BASE_URL in .env, or model.base_url in trypro.yaml.",
                retryable=False,
            )
        return OpenAICompatibleClient(
            base_url=config.base_url,
            api_key_env=config.api_key_env,
            max_tokens_param=config.max_tokens_param,
        )
    raise ClientError(f"unknown model provider '{config.provider}'", retryable=False)


@dataclass(frozen=True)
class ResolvedRoute:
    """Where one role's model calls go. Holds the name of the key variable, never its value."""

    provider: str
    base_url: str | None
    api_key_env: str | None
    model: str
    overridden: bool = False


class ClientRouter:
    """Resolves each role to a client and model: role override, then department, then default.

    Clients are built on first use, so a missing key for a department that never runs
    costs nothing. Installed is not invoked.
    """

    def __init__(self, config: ModelConfig, org: Org) -> None:
        self.config = config
        self.org = org
        self._clients: dict[tuple[str, str | None, str | None], ChatClient] = {}

    def resolve(self, role: Role) -> ResolvedRoute:
        override = self.org.llm_override_for(role.ref)
        base_url = override.get("base_url", self.config.base_url)
        provider = "openai_compatible" if "base_url" in override else self.config.provider
        model = override.get("model") or self.config.model_for_tier(role.model_tier)
        return ResolvedRoute(
            provider=provider,
            base_url=base_url,
            api_key_env=override.get("api_key_env", self.config.api_key_env),
            model=model,
            overridden=bool(override),
        )

    def route(self, role: Role) -> tuple[ChatClient, str]:
        resolved = self.resolve(role)
        key = (resolved.provider, resolved.base_url, resolved.api_key_env)
        if key not in self._clients:
            self._clients[key] = build_client(
                ModelConfig(
                    provider=resolved.provider,
                    tiers={},
                    api_key_env=resolved.api_key_env,
                    base_url=resolved.base_url,
                    effort=self.config.effort,
                    fallbacks=self.config.fallbacks,
                    max_tokens_param=self.config.max_tokens_param,
                )
            )
        return self._clients[key], resolved.model
