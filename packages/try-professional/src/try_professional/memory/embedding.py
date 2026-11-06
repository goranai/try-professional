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

Vectors = NDArray[np.float32]


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


class FastEmbedEmbedder:
    """Loads the model on first use, so importing this costs nothing."""

    def __init__(self, model_name: str = DEFAULT_MODEL, cache_dir: Path | None = None) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir or model_cache_dir()
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self._model = TextEmbedding(model_name=self.model_name, cache_dir=str(self.cache_dir))
            return self._model

    def _embed(self, texts: list[str]) -> Vectors:
        if not texts:
            return np.zeros((0, MODEL_DIMENSIONS.get(self.model_name, 1)), dtype=np.float32)
        model = self._load()
        return normalize(np.array(list(model.embed(texts))))

    def embed_documents(self, texts: list[str]) -> Vectors:
        prefix = model_prefixes(self.model_name)[1]
        return self._embed([f"{prefix}{text}" for text in texts])

    def embed_query(self, text: str) -> Vectors:
        prefix = model_prefixes(self.model_name)[0]
        vector: Vectors = self._embed([f"{prefix}{text}"])[0]
        return vector
