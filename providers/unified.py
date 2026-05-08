import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import pybreaker

from providers.budget import BudgetExceededError, get_current_guard
from providers.resilience import (
    ProviderBreakerRegistry,
    call_with_resilience,
    get_default_registry,
)

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    content: str
    provider_used: str
    model: str
    mode: str
    latency_ms: float
    cost_estimate_usd: float
    fallback_used: bool = False
    dry_run: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


COST_PER_1K_TOKENS: Dict[str, float] = {
    "anthropic": 0.015,
    "openai": 0.005,
    "deepseek": 0.0014,
}

ALL_PROVIDERS = ["anthropic", "openai", "deepseek"]

DEFAULT_MODELS: Dict[str, str] = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o-mini",
    "deepseek": "deepseek-chat",
}


def _generate_placeholder_json(schema: Dict[str, Any]) -> str:
    props = schema.get("properties", {})
    required = schema.get("required", [])
    result: Dict[str, Any] = {}
    for key, prop in props.items():
        prop_type = prop.get("type", "string")
        if prop_type == "string":
            result[key] = "[DRY_RUN]" if key in required else ""
        elif prop_type in ("integer", "number"):
            result[key] = 0
        elif prop_type == "boolean":
            result[key] = False
        elif prop_type == "array":
            result[key] = []
        elif prop_type == "object":
            result[key] = {}
    return json.dumps(result)


def _read_mode_config(mode: str, dry_run: bool = False) -> tuple[str, str]:
    from core.config import get_settings

    settings = get_settings()
    key = mode.upper()
    mode_lower = mode.lower()
    if mode_lower == "auto":
        return _read_mode_config("balanced", dry_run=dry_run)

    provider: str = getattr(settings, f"thesis_{mode_lower}_provider", "").strip().lower()
    model: str = getattr(settings, f"thesis_{mode_lower}_model", "").strip()

    if not provider:
        if dry_run:
            defaults = {"quality": "anthropic", "balanced": "openai", "cheap": "deepseek"}
            provider = defaults.get(mode_lower, "openai")
        else:
            raise RuntimeError(
                f"THESIS_{key}_PROVIDER is not set. "
                f"Add THESIS_{key}_PROVIDER and THESIS_{key}_MODEL to your .env file."
            )
    if not model:
        model = DEFAULT_MODELS.get(provider, "unknown")
    return provider, model


class ProviderHealthCache:
    def __init__(self, ttl_seconds: int = 60):
        self.ttl = ttl_seconds
        self._cache: Dict[str, tuple[bool, float]] = {}

    def is_healthy(self, provider: str) -> bool:
        entry = self._cache.get(provider)
        if entry is None:
            return True
        healthy, timestamp = entry
        if time.monotonic() - timestamp > self.ttl:
            del self._cache[provider]
            return True
        return healthy

    def mark_unhealthy(self, provider: str) -> None:
        self._cache[provider] = (False, time.monotonic())

    def mark_healthy(self, provider: str) -> None:
        if provider in self._cache:
            del self._cache[provider]


class UnifiedLLM:
    def __init__(
        self,
        blackboard: Any = None,
        breaker_registry: Optional[ProviderBreakerRegistry] = None,
    ) -> None:
        from core.config import get_settings

        self.dry_run = get_settings().dry_run
        self.blackboard = blackboard
        self.health_cache = ProviderHealthCache()
        self.breakers = breaker_registry or get_default_registry()
        self._session_spend: Dict[str, float] = {}
        self._generate_fn: Optional[Callable[..., Any]] = None
        self._available_providers: List[str] = []

    def validate_startup(self) -> None:
        if self.dry_run:
            return
        from core.config import get_settings

        settings = get_settings()
        for mode in ("quality", "balanced", "cheap"):
            key = mode.upper()
            if not getattr(settings, f"thesis_{mode}_provider", ""):
                raise RuntimeError(
                    f"THESIS_{key}_PROVIDER is not set. "
                    f"Configure THESIS_{key}_PROVIDER and THESIS_{key}_MODEL in your .env file. "
                    f"See Phase 4 docs for provider routing configuration."
                )

    def set_generate_fn(self, fn: Callable[..., Any]) -> None:
        self._generate_fn = fn

    def set_available_providers(self, providers: List[str]) -> None:
        self._available_providers = providers

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        mode: str = "auto",
        budget: Optional[float] = None,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        preferred_provider, preferred_model = _read_mode_config(mode, dry_run=self.dry_run)
        t0 = time.monotonic()

        if self.dry_run:
            elapsed = (time.monotonic() - t0) * 1000
            content = f"[DRY_RUN mode={mode} provider={preferred_provider} model={preferred_model}] Would call {preferred_provider}/{preferred_model}. Prompt: {prompt[:80]}..."
            if response_schema:
                content = _generate_placeholder_json(response_schema)
            response = LLMResponse(
                content=content,
                provider_used=preferred_provider,
                model=preferred_model,
                mode=mode,
                latency_ms=elapsed,
                cost_estimate_usd=0.0,
                dry_run=True,
                metadata={
                    "preferred_provider": preferred_provider,
                    "preferred_model": preferred_model,
                    "available_providers": self._available_providers,
                    "skipped_due_to_health": [],
                    "reason": f"mode={mode} preferred={preferred_provider}/{preferred_model}",
                    "structured_output": response_schema is not None,
                },
            )
            logger.info(
                f"ROUTE: dry_run mode={mode} -> {preferred_provider}/{preferred_model} "
                f"(available={self._available_providers})"
            )
            return response

        order = self._build_try_order(preferred_provider, mode)
        reason = f"mode={mode} preferred={preferred_provider}/{preferred_model}"
        skipped: List[str] = []
        last_error: Optional[Exception] = None

        for provider in order:
            if self.breakers.is_open(provider):
                skipped.append(provider)
                logger.info(f"ROUTE: skipping {provider} (circuit breaker open)")
                continue

            if not self.health_cache.is_healthy(provider):
                skipped.append(provider)
                logger.info(f"ROUTE: skipping {provider} (health check failed)")
                continue

            if budget is not None and self._get_spend(provider) >= budget:
                skipped.append(provider)
                logger.info(f"ROUTE: skipping {provider} (budget exhausted)")
                continue

            # Hard ceiling pre-check: if a session-scoped BudgetGuard is
            # installed, refuse to even attempt the call when its estimate
            # would push spend past the cap. The fallback chain still
            # runs — a cheaper provider downstream may fit even when this
            # one doesn't.
            guard = get_current_guard()
            if guard is not None and guard.enabled:
                pre_estimate = guard.estimate(prompt, system_prompt, provider)
                if not guard.check(pre_estimate):
                    skipped.append(provider)
                    last_error = BudgetExceededError(
                        f"{provider} pre-call estimate ${pre_estimate:.6f} would "
                        f"exceed remaining budget ${guard.remaining():.6f}"
                    )
                    logger.info(
                        f"ROUTE: skipping {provider} (over budget: estimate "
                        f"${pre_estimate:.6f}, remaining ${guard.remaining():.6f})"
                    )
                    continue

            model = (
                preferred_model
                if provider == preferred_provider
                else self._default_model_for(provider)
            )

            # Each per-provider attempt now goes through (breaker → retry).
            # Transient failures retry inside this call; sustained failure
            # trips the breaker so subsequent calls in this and future
            # sessions skip the provider until the reset timeout expires.
            current_provider = provider
            current_model = model

            async def _attempt() -> str:
                return await self._call_provider(
                    current_provider, current_model, prompt, system_prompt, response_schema
                )

            try:
                result = await call_with_resilience(
                    _attempt,
                    provider=provider,
                    registry=self.breakers,
                )
                elapsed = (time.monotonic() - t0) * 1000
                cost = self._estimate_cost(result, provider)

                if budget is not None:
                    self._add_spend(provider, cost)
                if guard is not None:
                    guard.record_actual(cost)

                fallback = provider != preferred_provider
                response = LLMResponse(
                    content=result,
                    provider_used=provider,
                    model=model,
                    mode=mode,
                    latency_ms=elapsed,
                    cost_estimate_usd=cost,
                    fallback_used=fallback,
                    metadata={
                        "preferred_provider": preferred_provider,
                        "preferred_model": preferred_model,
                        "try_order": order,
                        "skipped_due_to_health": skipped,
                        "reason": reason,
                        "last_error": str(last_error) if last_error else None,
                        "structured_output": response_schema is not None,
                    },
                )
                self.health_cache.mark_healthy(provider)
                logger.info(
                    f"ROUTE: mode={mode} -> {provider}/{model} "
                    f"(preferred={preferred_provider}/{preferred_model}) "
                    f"latency={elapsed:.1f}ms cost=${cost:.6f} fallback={fallback}"
                )
                return response
            except pybreaker.CircuitBreakerError as e:
                last_error = e
                skipped.append(provider)
                logger.warning(
                    f"ROUTE: {provider} short-circuited mid-call for mode={mode}. Falling back."
                )
            except Exception as e:
                last_error = e
                self.health_cache.mark_unhealthy(provider)
                logger.warning(
                    f"ROUTE: {provider}/{model} failed for mode={mode}: {e}. Falling back."
                )

        elapsed = (time.monotonic() - t0) * 1000
        logger.error(f"ROUTE: all providers failed for mode={mode}: {last_error}")
        return LLMResponse(
            content=f"[ALL_PROVIDERS_FAILED] {last_error}",
            provider_used="none",
            model="none",
            mode=mode,
            latency_ms=elapsed,
            cost_estimate_usd=0.0,
            fallback_used=True,
            metadata={
                "preferred_provider": preferred_provider,
                "preferred_model": preferred_model,
                "try_order": order,
                "last_error": str(last_error) if last_error else None,
            },
        )

    def _build_try_order(self, preferred: str, mode: str) -> List[str]:
        available = self._available_providers if self._available_providers else ALL_PROVIDERS
        order: List[str] = []
        if preferred in available:
            order.append(preferred)
        for p in available:
            if p not in order:
                order.append(p)
        return order

    async def _call_provider(
        self,
        provider: str,
        model: str,
        prompt: str,
        system_prompt: str,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> str:
        if self._generate_fn:
            result: str = await self._generate_fn(
                provider, model, prompt, system_prompt, response_schema
            )
            return result
        return f"[NO_PROVIDER] {provider}/{model} unavailable"

    def _default_model_for(self, provider: str) -> str:
        return DEFAULT_MODELS.get(provider, "unknown")

    def _estimate_cost(self, text: str, provider: str) -> float:
        tokens = len(text) / 3.5
        rate = COST_PER_1K_TOKENS.get(provider, 0.005)
        return (tokens / 1000) * rate

    def _get_spend(self, provider: str) -> float:
        return self._session_spend.get(provider, 0.0)

    def _add_spend(self, provider: str, amount: float) -> None:
        self._session_spend[provider] = self._get_spend(provider) + amount
