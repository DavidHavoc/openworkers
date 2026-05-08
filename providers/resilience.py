"""Per-provider retry + circuit-breaker primitives.

Two complementary mechanisms protect against flaky LLM providers:

1. **Tenacity retry** — when a single call fails with a *transient* error
   (timeout, 429, 5xx, network blip), retry it with exponential backoff and
   random jitter. Most provider hiccups are sub-second; one or two retries
   recover them invisibly.

2. **pybreaker circuit breaker** — when a provider has failed N times in
   a row across recent calls, the breaker opens and short-circuits further
   calls for ``reset_timeout`` seconds. Without this, a sustained outage
   causes every research session to wait for full per-call retry budgets
   to exhaust before falling over to the next provider in the chain.

The two compose: retries handle the noise inside each call; the breaker
trips when the noise crosses a threshold and stops the bleeding.

Configuration
-------------
``RESILIENCE_RETRY_ATTEMPTS``        (default 3)        — max attempts per call
``RESILIENCE_RETRY_BASE_SEC``        (default 0.5)      — exponential base
``RESILIENCE_RETRY_MAX_SEC``         (default 8.0)      — cap on backoff wait
``RESILIENCE_BREAKER_FAIL_MAX``      (default 5)        — consecutive failures before tripping
``RESILIENCE_BREAKER_RESET_SEC``     (default 60)       — seconds open before HALF_OPEN
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

import httpx
import pybreaker
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

logger = logging.getLogger(__name__)
_UTC = timezone.utc


# ── error classification ─────────────────────────────────────────────────


def is_transient_error(exc: BaseException) -> bool:
    """True if ``exc`` represents a problem that's worth retrying.

    Permanent errors (auth, malformed request, content filter) raise
    instantly and burn the next provider in the fallback chain — retrying
    them would just delay the inevitable. Transient errors (timeouts, rate
    limits, transient 5xx, network blips) recover on retry.
    """
    if isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.WriteError,
            httpx.PoolTimeout,
        ),
    ):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or 500 <= status < 600
    # Provider SDKs (anthropic, openai) wrap their own status codes in
    # exceptions whose names end with the kind of error. We only get
    # signal from the *type* in stable cases; otherwise fall back to a
    # message-substring check on a small allowlist of well-known phrases.
    name = type(exc).__name__.lower()
    if "timeout" in name or "ratelimit" in name or "service" in name:
        return True
    msg = str(exc).lower()
    if any(token in msg for token in ("timeout", "rate limit", "503", "504", "502", "429")):
        return True
    return False


# ── tenacity retry helper ────────────────────────────────────────────────


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


async def call_with_retry(
    fn: Callable[[], Awaitable[Any]],
    *,
    attempts: Optional[int] = None,
    base: Optional[float] = None,
    cap: Optional[float] = None,
    label: str = "provider_call",
) -> Any:
    """Run ``fn`` with exponential-jitter retry on transient errors.

    Deliberately uses ``AsyncRetrying`` (the imperative tenacity API)
    rather than the ``@retry`` decorator so the retry policy can be
    swapped per-call without rebuilding closures.
    """
    attempts = attempts if attempts is not None else _env_int("RESILIENCE_RETRY_ATTEMPTS", 3)
    base = base if base is not None else _env_float("RESILIENCE_RETRY_BASE_SEC", 0.5)
    cap = cap if cap is not None else _env_float("RESILIENCE_RETRY_MAX_SEC", 8.0)

    retryer = AsyncRetrying(
        stop=stop_after_attempt(max(1, attempts)),
        wait=wait_random_exponential(multiplier=base, max=cap),
        retry=retry_if_exception(is_transient_error),
        reraise=True,
    )
    attempt_no = 0
    async for attempt in retryer:
        with attempt:
            attempt_no += 1
            try:
                return await fn()
            except Exception as e:  # noqa: BLE001
                if attempt_no <= attempts and is_transient_error(e):
                    logger.warning(
                        "RETRY: %s attempt %d/%d failed transiently: %s",
                        label,
                        attempt_no,
                        attempts,
                        e,
                    )
                raise


# ── circuit breaker registry ─────────────────────────────────────────────


class _BreakerListener(pybreaker.CircuitBreakerListener):
    """Logs state transitions so users can see why providers got skipped."""

    def __init__(self, provider: str) -> None:
        self.provider = provider

    def state_change(
        self,
        cb: pybreaker.CircuitBreaker,
        old_state: Optional[pybreaker.CircuitBreakerState],
        new_state: pybreaker.CircuitBreakerState,
    ) -> None:
        old_name = getattr(old_state, "name", str(old_state))
        new_name = getattr(new_state, "name", str(new_state))
        logger.info(
            "BREAKER: %s %s -> %s (fail_count=%s)",
            self.provider,
            old_name,
            new_name,
            cb.fail_counter,
        )


class ProviderBreakerRegistry:
    """One CircuitBreaker per provider, lazily constructed.

    pybreaker tracks state in-process. For multi-process deployments each
    worker has its own breaker — that's fine for our use case (a single
    flaky provider trips quickly across workers since each independently
    sees the failures).
    """

    def __init__(
        self,
        fail_max: Optional[int] = None,
        reset_timeout_sec: Optional[int] = None,
    ) -> None:
        self.fail_max = (
            fail_max if fail_max is not None else _env_int("RESILIENCE_BREAKER_FAIL_MAX", 5)
        )
        self.reset_timeout = (
            reset_timeout_sec
            if reset_timeout_sec is not None
            else _env_int("RESILIENCE_BREAKER_RESET_SEC", 60)
        )
        self._breakers: dict[str, pybreaker.CircuitBreaker] = {}

    def for_provider(self, provider: str) -> pybreaker.CircuitBreaker:
        cb = self._breakers.get(provider)
        if cb is None:
            cb = pybreaker.CircuitBreaker(
                fail_max=self.fail_max,
                reset_timeout=self.reset_timeout,
                listeners=[_BreakerListener(provider)],
                # Permanent errors (auth, schema) shouldn't trip the breaker;
                # the fallback chain will handle a misconfigured provider on
                # its own. Only transient failures count toward fail_max.
                exclude=[lambda exc: not is_transient_error(exc)],
                name=f"provider:{provider}",
            )
            self._breakers[provider] = cb
        return cb

    def is_open(self, provider: str) -> bool:
        return self.for_provider(provider).current_state == pybreaker.STATE_OPEN

    def reset(self, provider: Optional[str] = None) -> None:
        """Force-close one or all breakers. Test hook, also useful for ops."""
        if provider:
            self._breakers.pop(provider, None)
            return
        self._breakers.clear()


_default_registry: Optional[ProviderBreakerRegistry] = None


def get_default_registry() -> ProviderBreakerRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = ProviderBreakerRegistry()
    return _default_registry


def reset_default_registry() -> None:
    """Test hook — discard the singleton so the next get() re-reads env."""
    global _default_registry
    _default_registry = None


# ── combined helper ──────────────────────────────────────────────────────


async def _async_breaker_call(
    breaker: pybreaker.CircuitBreaker, fn: Callable[[], Awaitable[Any]]
) -> Any:
    """Async-safe replacement for ``pybreaker.CircuitBreaker.call_async``.

    The shipped ``call_async`` in pybreaker 1.4.1 uses a missing
    ``gen.coroutine`` symbol from tornado and crashes immediately. We
    replicate the sync state machine ourselves: check OPEN/HALF-OPEN state
    before the call, await the function, then drive success/failure
    counters through the breaker's own listeners + state-storage hooks.
    """
    if breaker.current_state == pybreaker.STATE_OPEN:
        opened_at = breaker._state_storage.opened_at
        if opened_at and datetime.now(_UTC) < opened_at + timedelta(seconds=breaker.reset_timeout):
            raise pybreaker.CircuitBreakerError(
                "Timeout not elapsed yet, circuit breaker still open"
            )
        breaker.half_open()

    for before_listener in breaker.listeners:
        before_listener.before_call(breaker, fn)

    try:
        result = await fn()
    except BaseException as exc:
        if breaker.is_system_error(exc):
            breaker._state_storage.increment_counter()
            for fail_listener in breaker.listeners:
                fail_listener.failure(breaker, exc)
            if breaker.fail_counter >= breaker.fail_max:
                breaker.open()
        else:
            # Excluded exceptions (auth errors, schema errors) are not
            # held against the provider — they would still hit even on a
            # perfectly healthy backend. Reset the counter so transient
            # successes between configuration mistakes don't pile up.
            breaker._state_storage.reset_counter()
        raise

    breaker._state_storage.reset_counter()
    for success_listener in breaker.listeners:
        success_listener.success(breaker)
    if breaker.current_state == pybreaker.STATE_HALF_OPEN:
        breaker.close()
    return result


async def call_with_resilience(
    fn: Callable[[], Awaitable[Any]],
    *,
    provider: str,
    registry: Optional[ProviderBreakerRegistry] = None,
    attempts: Optional[int] = None,
    base: Optional[float] = None,
    cap: Optional[float] = None,
) -> Any:
    """Run ``fn`` through (breaker → retry) for ``provider``.

    Order matters: if the breaker is OPEN, we fail fast without burning
    the retry budget. Otherwise, retry within the breaker's protection.

    A short-circuited call raises ``pybreaker.CircuitBreakerError``; the
    caller's fallback chain should treat that the same as any other
    provider failure and move to the next provider in the chain.
    """
    reg = registry or get_default_registry()
    breaker = reg.for_provider(provider)

    async def _retried() -> Any:
        return await call_with_retry(fn, attempts=attempts, base=base, cap=cap, label=provider)

    return await _async_breaker_call(breaker, _retried)
