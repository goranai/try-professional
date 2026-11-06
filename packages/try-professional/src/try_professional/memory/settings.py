"""Mnemosyne settings this project depends on, and the check that keeps text local.

Evidence for each value is in docs/architecture.md, sections 8.3 and 15.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path

from .embedding import DEFAULT_MODEL, MODEL_DIMENSIONS, model_cache_dir, model_prefixes

_TRUE = {"1", "true", "yes", "on"}
# Long enough that nothing expires in practice. Organizational memory must not vanish
# after a week, and consolidating it instead could involve an LLM.
NO_EXPIRY_HOURS = "876000"
MAX_ITEMS = "1000000"
CONTENT_CAP = "4000"


class MemoryConfigError(RuntimeError):
    """Memory can't start safely. The message says what to change."""


class RemoteEmbeddingRefused(MemoryConfigError):
    """Something would send memory text to a remote API, and that wasn't allowed."""


@dataclass(frozen=True)
class MemorySettings:
    data_dir: Path
    embedding_model: str = DEFAULT_MODEL
    remote_allowed: bool = False
    cache_dir: Path | None = None

    @property
    def dimension(self) -> int:
        try:
            return MODEL_DIMENSIONS[self.embedding_model]
        except KeyError:
            raise MemoryConfigError(
                f"unknown embedding model '{self.embedding_model}'. Known: {', '.join(MODEL_DIMENSIONS)}"
            ) from None


def remote_embedding_reasons(model: str, env: Mapping[str, str]) -> list[str]:
    """Every setting that would make Mnemosyne call a remote embedding API."""
    reasons = []
    if model.startswith("openai/") or "text-embedding" in model:
        reasons.append(f"the model name '{model}' routes to a remote API")
    if (env.get("MNEMOSYNE_EMBEDDING_API_URL") or "").strip():
        reasons.append("MNEMOSYNE_EMBEDDING_API_URL is set")
    if (env.get("MNEMOSYNE_EMBEDDINGS_VIA_API") or "").strip().lower() in _TRUE:
        reasons.append("MNEMOSYNE_EMBEDDINGS_VIA_API is on")
    return reasons


def check_local_only(settings: MemorySettings, env: Mapping[str, str]) -> None:
    if settings.remote_allowed:
        return
    reasons = remote_embedding_reasons(settings.embedding_model, env)
    if reasons:
        raise RemoteEmbeddingRefused(
            "Refusing to start memory, because text would leave this machine: "
            + "; ".join(reasons)
            + ". Remove that setting, or set memory.remote_allowed: true in trypro.yaml."
        )


def apply_to_environment(settings: MemorySettings, env: MutableMapping[str, str] | None = None) -> None:
    """Set what Mnemosyne reads. Some of it is read once at import, so call this before importing it."""
    target = os.environ if env is None else env
    check_local_only(settings, target)
    query_prefix, document_prefix = model_prefixes(settings.embedding_model)
    target.update(
        {
            "MNEMOSYNE_DATA_DIR": str(settings.data_dir),
            "MNEMOSYNE_EMBEDDING_MODEL": settings.embedding_model,
            "MNEMOSYNE_EMBEDDING_DIM": str(settings.dimension),
            "MNEMOSYNE_EMBEDDING_QUERY_PREFIX": query_prefix,
            "MNEMOSYNE_EMBEDDING_DOC_PREFIX": document_prefix,
            "MNEMOSYNE_FASTEMBED_CACHE_DIR": str(settings.cache_dir or model_cache_dir()),
            # The default gate requires keyword overlap, so cross-language queries return nothing.
            "MNEMOSYNE_LEXICAL_GATE_MIN": "0",
            "MNEMOSYNE_WM_TTL_HOURS": NO_EXPIRY_HOURS,
            "MNEMOSYNE_WM_MAX_ITEMS": MAX_ITEMS,
            "MNEMOSYNE_RECALL_CONTENT_CAP": CONTENT_CAP,
            "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
        }
    )


def verify_mnemosyne_is_local(settings: MemorySettings) -> None:
    """Ask Mnemosyne itself, after import. It falls back to keywords silently if embeddings fail."""
    from mnemosyne.core import beam, embeddings

    # Its config.yaml can outrank the environment, so check what actually took effect.
    if settings.embedding_model != embeddings._DEFAULT_MODEL:
        raise MemoryConfigError(
            f"Mnemosyne is using '{embeddings._DEFAULT_MODEL}', not '{settings.embedding_model}'. "
            "Check for another embedding_model in its config.yaml, or an earlier import in this process."
        )
    if int(NO_EXPIRY_HOURS) > beam.WORKING_MEMORY_TTL_HOURS:
        raise MemoryConfigError(
            f"Mnemosyne would delete memories after {beam.WORKING_MEMORY_TTL_HOURS} hours. "
            "Check for wm_ttl_hours in its config.yaml, or an earlier import in this process."
        )
    if not settings.remote_allowed and embeddings._is_api_model(settings.embedding_model):
        raise RemoteEmbeddingRefused(
            f"Mnemosyne would send text for '{settings.embedding_model}' to a remote API. Refusing to start memory."
        )
    if not embeddings.available():
        raise MemoryConfigError(
            "Mnemosyne has no embedding engine, so cross-language recall would silently stop working. "
            "Install it with: pip install 'mnemosyne-memory[embeddings]'"
        )


def check_model_unchanged(settings: MemorySettings) -> None:
    """Vectors from different models can't be compared, so a model change must not pass silently."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    meta = settings.data_dir / "trypro-memory.json"
    if meta.is_file():
        recorded = json.loads(meta.read_text(encoding="utf-8")).get("embedding_model")
        if recorded != settings.embedding_model:
            raise MemoryConfigError(
                f"This memory was built with '{recorded}', but the configured model is "
                f"'{settings.embedding_model}'. Their vectors can't be compared. Switch back, or move "
                f"{settings.data_dir} aside to start fresh."
            )
        return
    meta.write_text(json.dumps({"embedding_model": settings.embedding_model}), encoding="utf-8", newline="\n")
