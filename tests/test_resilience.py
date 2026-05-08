"""Tests for the per-provider retry + circuit-breaker layer.

Covers three layers:

1. **Error classification** (``is_transient_error``) — what counts as
   retryable. The list is heuristic; this test pins down current
   behaviour so future changes are deliberate.
2. **Retry helper** (``call_with_retry``) — transient errors retry up to
   the attempt cap; permanent errors raise instantly.
3. **Breaker registry** + ``call_with_resilience`` — a provider that
   keeps failing trips its breaker after ``fail_max`` consecutive
   transient failures. Subsequent calls short-circuit with
   ``CircuitBreakerError``. Permanent errors do not count toward the
   threshold (fallback-chain handles misconfig).
"""

from __future__ import annotations

import asyncio

import httpx
import pybreaker
import pytest

from providers import resilience
from providers.resilience import (
    ProviderBreakerRegistry,
    call_with_resilience,
    call_with_retry,
    is_transient_error,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    """Each test gets a fresh module-level breaker registry."""
    resilience.reset_default_registry()
    yield
    resilience.reset_default_registry()


# ──────────────────────────────────────────────────────────────────────────
# error classification
# ──────────────────────────────────────────────────────────────────────────


def test_transient_classifies_httpx_timeouts():
    assert is_transient_error(httpx.ConnectTimeout("slow"))
    assert is_transient_error(httpx.ReadTimeout("slow"))
    assert is_transient_error(httpx.PoolTimeout("slow"))


def test_transient_classifies_5xx():
    req = httpx.Request("GET", "https://x")
    for code in (500, 502, 503, 504):
        resp = httpx.Response(code, request=req)
        assert is_transient_error(httpx.HTTPStatusError("err", request=req, response=resp))


def test_transient_classifies_429():
    req = httpx.Request("GET", "https://x")
    resp = httpx.Response(429, request=req)
    assert is_transient_error(httpx.HTTPStatusError("rate", request=req, response=resp))


def test_permanent_4xx_not_transient():
    req = httpx.Request("GET", "https://x")
    for code in (400, 401, 403, 404, 422):
        resp = httpx.Response(code, request=req)
        assert not is_transient_error(httpx.HTTPStatusError("perm", request=req, response=resp))


def test_message_substring_fallback_recognises_rate_limit():
    """SDKs that don't subclass httpx still surface 'rate limit' in str()."""

    class FakeProviderError(Exception):
        pass

    assert is_transient_error(FakeProviderError("503 Service Unavailable"))
    assert is_transient_error(FakeProviderError("rate limit exceeded"))


def test_unknown_exception_treated_as_permanent():
    assert not is_transient_error(ValueError("malformed prompt"))
    assert not is_transient_error(KeyError("missing api key"))


# ──────────────────────────────────────────────────────────────────────────
# call_with_retry
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_returns_on_first_success():
    calls = 0

    async def ok():
        nonlocal calls
        calls += 1
        return "result"

    out = await call_with_retry(ok, attempts=3, base=0.0, cap=0.0)
    assert out == "result"
    assert calls == 1


@pytest.mark.asyncio
async def test_retry_recovers_after_transient_failure():
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ReadTimeout("blip")
        return "ok"

    out = await call_with_retry(flaky, attempts=5, base=0.0, cap=0.0)
    assert out == "ok"
    assert calls == 3


@pytest.mark.asyncio
async def test_retry_gives_up_after_attempt_cap():
    calls = 0

    async def always_flaky():
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("permanent blip")

    with pytest.raises(httpx.ReadTimeout):
        await call_with_retry(always_flaky, attempts=3, base=0.0, cap=0.0)
    assert calls == 3


@pytest.mark.asyncio
async def test_retry_does_not_retry_permanent_errors():
    """A 401 must raise instantly — the next provider can try."""
    calls = 0

    async def auth_fail():
        nonlocal calls
        calls += 1
        req = httpx.Request("GET", "https://x")
        resp = httpx.Response(401, request=req)
        raise httpx.HTTPStatusError("unauthorized", request=req, response=resp)

    with pytest.raises(httpx.HTTPStatusError):
        await call_with_retry(auth_fail, attempts=5, base=0.0, cap=0.0)
    assert calls == 1, "permanent errors must not retry"


# ──────────────────────────────────────────────────────────────────────────
# circuit breaker
# ──────────────────────────────────────────────────────────────────────────


def test_registry_returns_same_breaker_per_provider():
    reg = ProviderBreakerRegistry()
    a1 = reg.for_provider("openai")
    a2 = reg.for_provider("openai")
    b = reg.for_provider("anthropic")
    assert a1 is a2
    assert a1 is not b


def test_breaker_starts_closed():
    reg = ProviderBreakerRegistry(fail_max=3, reset_timeout_sec=60)
    assert not reg.is_open("openai")


@pytest.mark.asyncio
async def test_breaker_trips_after_fail_max_consecutive_transient_errors():
    reg = ProviderBreakerRegistry(fail_max=3, reset_timeout_sec=60)

    async def boom():
        raise httpx.ReadTimeout("blip")

    # Each call_with_resilience exhausts retries then raises ReadTimeout.
    # That single exhausted call counts as ONE failure to the breaker.
    for _ in range(3):
        with pytest.raises(httpx.ReadTimeout):
            await call_with_resilience(
                boom,
                provider="openai",
                registry=reg,
                attempts=1,
                base=0.0,
                cap=0.0,
            )

    assert reg.is_open("openai"), "breaker should be open after fail_max failures"


@pytest.mark.asyncio
async def test_breaker_short_circuits_when_open():
    reg = ProviderBreakerRegistry(fail_max=2, reset_timeout_sec=60)

    async def boom():
        raise httpx.ReadTimeout("blip")

    for _ in range(2):
        with pytest.raises(httpx.ReadTimeout):
            await call_with_resilience(
                boom, provider="openai", registry=reg, attempts=1, base=0.0, cap=0.0
            )

    assert reg.is_open("openai")

    calls = 0

    async def should_not_run():
        nonlocal calls
        calls += 1
        return "wont happen"

    with pytest.raises(pybreaker.CircuitBreakerError):
        await call_with_resilience(
            should_not_run, provider="openai", registry=reg, attempts=1, base=0.0, cap=0.0
        )
    assert calls == 0, "open breaker must not invoke the wrapped function"


@pytest.mark.asyncio
async def test_breaker_does_not_trip_on_permanent_errors():
    """Auth errors hit the fallback chain; they shouldn't open the breaker."""
    reg = ProviderBreakerRegistry(fail_max=2, reset_timeout_sec=60)

    async def auth_fail():
        req = httpx.Request("GET", "https://x")
        resp = httpx.Response(401, request=req)
        raise httpx.HTTPStatusError("unauthorized", request=req, response=resp)

    for _ in range(5):
        with pytest.raises(httpx.HTTPStatusError):
            await call_with_resilience(
                auth_fail, provider="openai", registry=reg, attempts=1, base=0.0, cap=0.0
            )

    assert not reg.is_open("openai"), "permanent errors must be excluded from breaker fail count"


@pytest.mark.asyncio
async def test_breaker_resets_after_reset_timeout():
    """After ``reset_timeout`` seconds, the breaker becomes HALF-OPEN."""
    reg = ProviderBreakerRegistry(fail_max=2, reset_timeout_sec=0)  # immediate reset

    async def boom():
        raise httpx.ReadTimeout("blip")

    for _ in range(2):
        with pytest.raises(httpx.ReadTimeout):
            await call_with_resilience(
                boom, provider="openai", registry=reg, attempts=1, base=0.0, cap=0.0
            )

    # With reset_timeout=0, the very next call enters HALF-OPEN and tries.
    await asyncio.sleep(0.01)

    async def ok():
        return "back"

    out = await call_with_resilience(
        ok, provider="openai", registry=reg, attempts=1, base=0.0, cap=0.0
    )
    assert out == "back"
    assert not reg.is_open("openai")


# ──────────────────────────────────────────────────────────────────────────
# UnifiedLLM integration: exhausted breaker on preferred → falls back
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unified_falls_back_when_preferred_breaker_open(monkeypatch):
    """Open breaker on the preferred provider triggers fallback in routing."""
    from providers.unified import UnifiedLLM

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("THESIS_BALANCED_PROVIDER", "openai")
    monkeypatch.setenv("THESIS_BALANCED_MODEL", "gpt-4o-mini")

    reg = ProviderBreakerRegistry(fail_max=1, reset_timeout_sec=60)

    # Pre-trip the openai breaker.
    breaker = reg.for_provider("openai")
    for _ in range(2):
        try:
            breaker.call(lambda: (_ for _ in ()).throw(httpx.ReadTimeout("preexisting")))
        except Exception:
            pass

    assert reg.is_open("openai")

    llm = UnifiedLLM(breaker_registry=reg)
    llm.set_available_providers(["openai", "anthropic"])

    called = []

    async def fake_generate(provider, model, prompt, system_prompt, response_schema):
        called.append(provider)
        if provider == "anthropic":
            return "anthropic-result"
        # If openai is invoked despite an open breaker, the test fails.
        raise httpx.ReadTimeout("should not reach")

    llm.set_generate_fn(fake_generate)

    response = await llm.generate(prompt="hi", mode="balanced")
    assert response.provider_used == "anthropic"
    assert response.fallback_used is True
    assert "openai" not in called
    assert "anthropic" in called
