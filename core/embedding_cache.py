"""Disk-level cache for FastEmbed embedding vectors.

FastEmbed already caches model weights on disk (``~/.cache/fastembed/``).
This module adds a second cache layer for the *outputs* — the dense vectors
produced by embedding a particular text string. On a warm cache a RAG query
skips the embedding computation entirely, cutting per-query overhead from
~20ms (CPU inference) to ~0.1ms (SQLite lookup). The cache survives container
restarts because it writes to a user-controlled directory on the host
filesystem.

Default location: ``~/.cache/openworkers/embeddings`` (overridable via the
``EMBEDDING_CACHE_DIR`` env var / ``Settings.embedding_cache_dir``).

The cache is a :class:`diskcache.Cache` backed by SQLite. Keys are
``sha256(model_name + NUL + text)`` hex digests; values are ``list[float]``.
Thread-safe (diskcache uses SQLite WAL mode) and safe to call from
``asyncio.to_thread``.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = str(Path.home() / ".cache" / "openworkers" / "embeddings")

_disk: Any | None = None  # diskcache.Cache
_models: dict[str, Any] = {}  # model_name → TextEmbedding instance


def _get_cache() -> Any | None:
    global _disk
    if _disk is not None:
        return _disk
    try:
        import diskcache
    except ImportError:
        logger.debug("diskcache not installed; embedding cache disabled")
        return None

    import os

    cache_dir = os.environ.get("EMBEDDING_CACHE_DIR", "") or _DEFAULT_CACHE_DIR
    _disk = diskcache.Cache(cache_dir)
    return _disk


def _get_model(model_name: str) -> Any:
    if model_name not in _models:
        from fastembed import TextEmbedding

        _models[model_name] = TextEmbedding(model_name)
    return _models[model_name]


def embed_text(text: str, model_name: str) -> list[float]:
    """Return the embedding vector for *text*, reading from / writing to disk cache.

    Safe to call from ``asyncio.to_thread`` — diskcache is thread-safe and
    the FastEmbed model object is not shared across threads (each thread uses
    the same in-memory instance but ``TextEmbedding.embed`` is GIL-safe for
    pure-numpy inference).
    """
    key = hashlib.sha256(f"{model_name}\x00{text}".encode()).hexdigest()
    cache = _get_cache()

    if cache is not None:
        raw = cache.get(key)
        if raw is not None:
            return list(raw)

    model = _get_model(model_name)
    result = next(model.embed([text]))  # numpy array
    vector: list[float] = result.tolist()

    if cache is not None:
        try:
            cache.set(key, vector)
        except Exception as exc:
            logger.debug("Embedding cache write failed: %s", exc)

    return vector


def reset_embedding_cache() -> None:
    """Test hook — drop the in-process cache handle and model instances."""
    global _disk, _models
    _disk = None
    _models = {}
