"""Tests for the Redis-backed search cache.

Uses ``fakeredis`` (already in dev deps) so tests run in-process without
needing a real Redis. Each test replaces the lazily-built default cache
client with one bound to a fresh fakeredis instance.
"""

from __future__ import annotations

from typing import Any

import fakeredis
import pytest

from tools import cache as cache_module
from tools.cache import CACHE_KEY_PREFIX, SearchCache, _canonical_key
from tools.mcp.engine import MCPTool


@pytest.fixture
def fake_redis(monkeypatch):
    """Inject a fresh fakeredis into both redis.from_url calls.

    Returns the FakeRedis instance so tests can poke at it directly
    (TTL inspection, key listing, etc.).
    """
    server = fakeredis.FakeServer()
    fake = fakeredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(
        "redis.from_url",
        lambda *_args, **_kwargs: fakeredis.FakeRedis(server=server, decode_responses=True),
    )
    cache_module.reset_default_cache()
    yield fake
    cache_module.reset_default_cache()


# ──────────────────────────────────────────────────────────────────────────
# canonical key
# ──────────────────────────────────────────────────────────────────────────


def test_canonical_key_is_order_independent():
    a = _canonical_key("arxiv_search", {"query": "x", "limit": 5})
    b = _canonical_key("arxiv_search", {"limit": 5, "query": "x"})
    assert a == b


def test_canonical_key_differs_per_tool():
    a = _canonical_key("arxiv_search", {"query": "x"})
    b = _canonical_key("semantic_scholar_search", {"query": "x"})
    assert a != b


def test_canonical_key_differs_per_param_value():
    a = _canonical_key("arxiv_search", {"query": "deep learning"})
    b = _canonical_key("arxiv_search", {"query": "machine learning"})
    assert a != b


def test_canonical_key_starts_with_prefix():
    key = _canonical_key("arxiv_search", {"query": "x"})
    assert key.startswith(CACHE_KEY_PREFIX)


# ──────────────────────────────────────────────────────────────────────────
# SearchCache get/set
# ──────────────────────────────────────────────────────────────────────────


def test_cache_set_then_get_round_trips(fake_redis):
    c = SearchCache()
    payload = {"papers": [{"id": "p1", "title": "Hello"}]}
    c.set("arxiv_search", {"query": "x"}, payload)
    assert c.get("arxiv_search", {"query": "x"}) == payload


def test_cache_miss_returns_none(fake_redis):
    c = SearchCache()
    assert c.get("arxiv_search", {"query": "never-seen"}) is None


def test_cache_does_not_store_error_payload(fake_redis):
    """Errors are transient. Caching them would lock out retries."""
    c = SearchCache()
    c.set("arxiv_search", {"query": "x"}, {"error": "rate limit"})
    assert c.get("arxiv_search", {"query": "x"}) is None


def test_cache_does_not_store_non_dict(fake_redis):
    c = SearchCache()
    c.set("arxiv_search", {"q": "x"}, "not a dict")  # type: ignore[arg-type]
    assert c.get("arxiv_search", {"q": "x"}) is None


def test_cache_respects_ttl(fake_redis):
    """The TTL we configure is the TTL Redis sees."""
    c = SearchCache(ttl_seconds=42)
    c.set("arxiv_search", {"query": "x"}, {"papers": []})
    key = _canonical_key("arxiv_search", {"query": "x"})
    assert fake_redis.ttl(key) <= 42
    assert fake_redis.ttl(key) > 0


def test_cache_disabled_via_env(monkeypatch, fake_redis):
    monkeypatch.setenv("SEARCH_CACHE_ENABLED", "false")
    cache_module.reset_default_cache()
    c = SearchCache()
    c.set("arxiv_search", {"q": "x"}, {"papers": []})
    # Nothing was actually written.
    assert fake_redis.dbsize() == 0
    assert c.get("arxiv_search", {"q": "x"}) is None


def test_cache_survives_redis_failure(monkeypatch):
    """If Redis is dead, the cache silently no-ops — never blows up."""

    def boom(*_args, **_kwargs):
        raise ConnectionError("Redis is down")

    monkeypatch.setattr("redis.from_url", boom)
    cache_module.reset_default_cache()

    c = SearchCache()
    # No raise on get/set even though backend is broken.
    c.set("arxiv_search", {"q": "x"}, {"papers": []})
    assert c.get("arxiv_search", {"q": "x"}) is None


def test_cache_invalidates_corrupt_entry(fake_redis):
    """Stale/corrupt JSON in Redis is dropped, not propagated."""
    c = SearchCache()
    key = _canonical_key("arxiv_search", {"q": "x"})
    fake_redis.set(key, "{not valid json")
    assert c.get("arxiv_search", {"q": "x"}) is None
    # Subsequent access should now miss cleanly.
    assert fake_redis.get(key) is None


def test_clear_namespace_removes_only_target_tool(fake_redis):
    c = SearchCache()
    c.set("arxiv_search", {"q": "a"}, {"papers": []})
    c.set("arxiv_search", {"q": "b"}, {"papers": []})
    c.set("semantic_scholar_search", {"q": "a"}, {"papers": []})

    deleted = c.clear_namespace("arxiv_search")
    assert deleted == 2
    assert c.get("arxiv_search", {"q": "a"}) is None
    assert c.get("semantic_scholar_search", {"q": "a"}) is not None


# ──────────────────────────────────────────────────────────────────────────
# MCPTool integration
# ──────────────────────────────────────────────────────────────────────────


class _FakeTool(MCPTool):
    name = "fake_search"
    cacheable = True
    allowed_tiers = ["public"]

    def __init__(self) -> None:
        self.call_count = 0

    def get_input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"query": {"type": "string"}}}

    def get_output_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"papers": {"type": "array"}}}

    async def execute_impl(self, params: dict[str, Any]) -> dict[str, Any]:
        self.call_count += 1
        return {"papers": [{"id": f"p{self.call_count}"}]}


class _FakeUncachedTool(_FakeTool):
    name = "fake_uncached"
    cacheable = False


@pytest.mark.asyncio
async def test_tool_execute_caches_successful_result(fake_redis):
    tool = _FakeTool()
    out1 = await tool.execute({"query": "x"}, "public")
    out2 = await tool.execute({"query": "x"}, "public")

    assert out1 == out2
    assert tool.call_count == 1, "second call should be served from cache"


@pytest.mark.asyncio
async def test_tool_execute_distinguishes_params(fake_redis):
    tool = _FakeTool()
    a = await tool.execute({"query": "alpha"}, "public")
    b = await tool.execute({"query": "beta"}, "public")
    assert a != b
    assert tool.call_count == 2


@pytest.mark.asyncio
async def test_tool_execute_does_not_cache_when_disabled(fake_redis):
    tool = _FakeUncachedTool()
    await tool.execute({"query": "x"}, "public")
    await tool.execute({"query": "x"}, "public")
    assert tool.call_count == 2, "uncached tool must run impl every call"


@pytest.mark.asyncio
async def test_tool_execute_does_not_cache_errors(fake_redis):
    """A raised exception becomes {"error": ...}; that must not be cached."""

    class _BoomTool(_FakeTool):
        name = "boom_tool"

        async def execute_impl(self, params: dict[str, Any]) -> dict[str, Any]:
            self.call_count += 1
            raise RuntimeError("upstream 503")

    tool = _BoomTool()
    out1 = await tool.execute({"query": "x"}, "public")
    out2 = await tool.execute({"query": "x"}, "public")
    assert "error" in out1 and "error" in out2
    # Both calls must hit execute_impl — failures are never cached.
    assert tool.call_count == 2


@pytest.mark.asyncio
async def test_tool_execute_blocks_disallowed_tier_before_cache(fake_redis):
    """Privacy enforcement runs before cache lookup.

    Caching a tier-allowed result then serving it to a tier-blocked caller
    would be a security bypass. We assert the security check happens first.
    """
    tool = _FakeTool()  # allowed_tiers=["public"]
    out = await tool.execute({"query": "x"}, "trusted")
    assert "error" in out and "Security Violation" in out["error"]
    assert tool.call_count == 0
