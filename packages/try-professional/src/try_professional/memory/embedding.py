"""Local multilingual embeddings. Text is embedded on this machine, and never sent anywhere."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_DIMENSIONS = {
    DEFAULT_MODEL: 384,
    "intfloat/multilingual-e5-large": 1024,
}
# (query prefix, document prefix). Applied verbatim: e5 models were trained with these.
MODEL_PREFIXES = {
    "intfloat/multilingual-e5-large": ("query: ", "passage: "),
}
# For memory recall only: the skill catalog has no floor. Chosen on the development split,
# per model: see docs/research/phase3-retrieval/. This is a weak filter. On the heldout split it
# rejected 7 of 10 nonsense queries, because scores for correct matches and nonsense overlap.
# It keeps the worst noise out of a task's context, and can't be relied on to detect "no match".
# A floor doesn't transfer between models, so a model with no measured floor gets none.
SCORE_FLOORS = {
    DEFAULT_MODEL: 0.28,
}

# Lowers peak memory while embedding, measured in docs/research/memory/. Padding stays small too.
EMBED_BATCH_SIZE = 8

Vectors = NDArray[np.float32]

_models: dict[str, Any] = {}
_models_lock = threading.Lock()


def model_cache_dir() -> Path:
    """Shared across projects, so each new company doesn't download the model again."""
    configured = os.environ.get("TRYPRO_MODEL_CACHE")
    if configured:
        return Path(configured)
    return Path.home() / ".cache" / "try-professional" / "fastembed"


def score_floor(model: str) -> float:
    return SCORE_FLOORS.get(model, 0.0)


def model_prefixes(model: str) -> tuple[str, str]:
    return MODEL_PREFIXES.get(model, ("", ""))


class Embedder(Protocol):
    model_name: str

    def embed_documents(self, texts: list[str]) -> Vectors:
        """Return one L2-normalized row per text."""
        ...

    def embed_query(self, text: str) -> Vectors:
        """Return one L2-normalized vector."""
        ...


def normalize(vectors: NDArray[Any]) -> Vectors:
    array = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    result: Vectors = array / norms
    return result


def shared_model(model_name: str, cache_dir: Path | None = None) -> Any:
    """The one copy in this process, whoever asks. It's about 600 MB, and every caller can use the same one."""
    with _models_lock:
        if model_name not in _models:
            from fastembed import TextEmbedding

            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            directory = cache_dir or model_cache_dir()
            directory.mkdir(parents=True, exist_ok=True)
            _models[model_name] = TextEmbedding(model_name=model_name, cache_dir=str(directory))
        return _models[model_name]


def model_is_loaded(model_name: str) -> bool:
    return model_name in _models


class SharedModel:
    """Stands in for a model in another library. It loads the shared copy on first use, and not before."""

    def __init__(self, model_name: str, cache_dir: Path | None = None) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir

    def embed(self, texts: Any, **options: Any) -> Any:
        options.setdefault("batch_size", EMBED_BATCH_SIZE)
        return shared_model(self.model_name, self.cache_dir).embed(texts, **options)


class FastEmbedEmbedder:
    """Loads the model on first use, so importing this costs nothing."""

    def __init__(self, model_name: str = DEFAULT_MODEL, cache_dir: Path | None = None) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir or model_cache_dir()

    def _embed(self, texts: list[str]) -> Vectors:
        if not texts:
            return np.zeros((0, MODEL_DIMENSIONS.get(self.model_name, 1)), dtype=np.float32)
        model = shared_model(self.model_name, self.cache_dir)
        return normalize(np.array(list(model.embed(texts, batch_size=EMBED_BATCH_SIZE))))

    def embed_documents(self, texts: list[str]) -> Vectors:
        prefix = model_prefixes(self.model_name)[1]
        return self._embed([f"{prefix}{text}" for text in texts])

    def embed_query(self, text: str) -> Vectors:
        prefix = model_prefixes(self.model_name)[0]
        vector: Vectors = self._embed([f"{prefix}{text}"])[0]
        return vector
