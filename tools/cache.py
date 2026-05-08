"""Redis-backed result cache for read-mostly external search APIs.

The arXiv, Semantic Scholar, and CrossRef endpoints return the same payload
for the same query for hours-to-days. Hitting them on every research session
wastes latency (each call: 200ms-2s) and pushes us toward upstream rate
limits. A 24-hour TTL strikes the usual balance: same student rerunning a
query reuses results; a different session next week sees fresh data.

Design notes
------------
* **Opt-in per tool.** ``MCPTool`` subclasses set ``cacheable = True`` to
  participate. RAG/knowledge-retrieval/structured-data are *not* cached
  because their backing state changes per-session.
* **Soft dependency on Redis.** If the Redis connection fails (no server
  running, network blip, auth error), ``get`` returns ``None`` and ``set``
  is a no-op. The caller proceeds as if no cache existed — never fails a
  research session because the cache is down.
* **Key shape.** ``ow:cache:{tool_name}:{sha256(canonical_params)}``. Params
  are canonicalised by ``json.dumps(sort_keys=True)`` so ``{"q":"a","n":5}``
  and ``{"n":5,"q":"a"}`` collide intentionally.
* **No error caching.** A response with an ``"error"`` key is never written
  to the cache — a transient failure must not lock out future successful
  retries for the rest of the TTL.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Optional

import redis

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "ow:cache:"
DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24 hours


def _canonical_key(tool_name: str, params: dict[str, Any]) -> str:
    """Build a deterministic cache key from tool name + params.

    Keys are sorted to make ``{"a":1,"b":2}`` and ``{"b":2,"a":1}`` collide.
    The hash gives us bounded key length even for long queries.
    """
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{CACHE_KEY_PREFIX}{tool_name}:{digest}"


class SearchCache:
    """Best-effort Redis cache. Never raises on Redis failure."""

    def __init__(
        self,
        redis_url: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self.redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self.ttl = (
            ttl_seconds
            if ttl_seconds is not None
            else int(os.environ.get("SEARCH_CACHE_TTL_SECONDS", str(DEFAULT_TTL_SECONDS)))
        )
        env_enabled = os.environ.get("SEARCH_CACHE_ENABLED", "true").lower() != "false"
        self.enabled = env_enabled if enabled is None else enabled
        self._client: Optional[redis.Redis] = None

    def _get_client(self) -> Optional[redis.Redis]:
        if not self.enabled:
            return None
        if self._client is None:
            try:
                self._client = redis.from_url(self.redis_url, decode_responses=True)
            except Exception as e:
                logger.warning("Search cache disabled — Redis init failed: %s", e)
                self.enabled = False
                return None
        return self._client

    def get(self, tool_name: str, params: dict[str, Any]) -> Optional[dict[str, Any]]:
        client = self._get_client()
        if client is None:
            return None
        key = _canonical_key(tool_name, params)
        try:
            raw = client.get(key)
        except Exception as e:
            logger.debug("Cache GET failed for %s: %s", key, e)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Stale or malformed entry — drop it so the next caller writes fresh.
            try:
                client.delete(key)
            except Exception:
                pass
            return None

    def set(self, tool_name: str, params: dict[str, Any], value: dict[str, Any]) -> None:
        if not isinstance(value, dict) or "error" in value:
            return
        client = self._get_client()
        if client is None:
            return
        key = _canonical_key(tool_name, params)
        try:
            client.setex(key, self.ttl, json.dumps(value))
        except Exception as e:
            logger.debug("Cache SET failed for %s: %s", key, e)

    def invalidate(self, tool_name: str, params: dict[str, Any]) -> None:
        client = self._get_client()
        if client is None:
            return
        try:
            client.delete(_canonical_key(tool_name, params))
        except Exception:
            pass

    def clear_namespace(self, tool_name: str) -> int:
        """Delete every key for a given tool. Returns count of deletions.

        Uses SCAN rather than KEYS so it remains safe on large Redis
        instances. Errors are swallowed and reported as 0.
        """
        client = self._get_client()
        if client is None:
            return 0
        pattern = f"{CACHE_KEY_PREFIX}{tool_name}:*"
        try:
            deleted = 0
            for key in client.scan_iter(match=pattern, count=500):
                client.delete(key)
                deleted += 1
            return deleted
        except Exception as e:
            logger.debug("Cache clear_namespace failed for %s: %s", tool_name, e)
            return 0


_default_cache: Optional[SearchCache] = None


def get_default_cache() -> SearchCache:
    """Process-wide singleton. Constructed lazily so tests can override env."""
    global _default_cache
    if _default_cache is None:
        _default_cache = SearchCache()
    return _default_cache


def reset_default_cache() -> None:
    """Force the next get_default_cache() call to re-read env. Test hook."""
    global _default_cache
    _default_cache = None
