"""Multilingual memory: department and company banks on Mnemosyne, behind access rules."""

from .backend import MemoryBackend, MemoryHit, MnemosyneBackend
from .embedding import DEFAULT_MODEL, Embedder, FastEmbedEmbedder, model_cache_dir, score_floor
from .gateway import COMPANY_BANK, MemoryAccessDenied, MemoryGateway, department_bank
from .settings import (
    MemoryConfigError,
    MemorySettings,
    RemoteEmbeddingRefused,
    check_local_only,
    remote_embedding_reasons,
)

__all__ = [
    "COMPANY_BANK",
    "DEFAULT_MODEL",
    "Embedder",
    "FastEmbedEmbedder",
    "MemoryAccessDenied",
    "MemoryBackend",
    "MemoryConfigError",
    "MemoryGateway",
    "MemoryHit",
    "MemorySettings",
    "MnemosyneBackend",
    "RemoteEmbeddingRefused",
    "check_local_only",
    "department_bank",
    "model_cache_dir",
    "remote_embedding_reasons",
    "score_floor",
]
