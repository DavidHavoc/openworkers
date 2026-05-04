import os
import asyncio
from typing import Dict, Any
import logging

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from core.schemas import Task
from providers.interfaces import HeadProvider, MiddleProvider, WorkerProvider
from providers.unified import UnifiedLLM
from core.orchestrator.compiler import PromptCompiler


_PROVIDER_API_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


class LLMAdapter:
    """Single-provider backend adapter. Called by UnifiedLLM, not by agents directly."""
    def __init__(self, provider: str, default_model: str = None):
        self.provider = provider
        self.dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"
        self.default_model = default_model or "unknown"
        self.api_key = os.environ.get(_PROVIDER_API_KEY_ENV.get(provider, ""))
        self.anthropic_client = None
        self.openai_client = None

        if self.provider == "anthropic":
            if not self.dry_run and self.api_key:
                self.anthropic_client = AsyncAnthropic(api_key=self.api_key)
        elif self.provider == "openai":
            if not self.dry_run and self.api_key:
                self.openai_client = AsyncOpenAI(api_key=self.api_key)
        elif self.provider == "deepseek":
            self.base_url = "https://api.deepseek.com"
            if not self.dry_run and self.api_key:
                self.openai_client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    def has_api_key(self) -> bool:
        return bool(self.api_key)

    async def generate(self, prompt: str, system_prompt: str = "", model: str = None) -> str:
        model_name = model or self.default_model

        if self.dry_run:
            await asyncio.sleep(0.01)
            return f"[{self.provider.upper()}/{model_name} DRY_RUN] Processed: {prompt[:50]}..."
        if not self.api_key:
            raise ValueError(f"No API key configured for provider '{self.provider}'")
        try:
            if self.provider == "anthropic":
                if self.anthropic_client is None:
                    raise ValueError(f"Anthropic client not initialized for '{self.provider}'")
                response = await asyncio.wait_for(
                    self.anthropic_client.messages.create(
                        model=model_name,
                        max_tokens=4096,
                        system=system_prompt,
                        messages=[{"role": "user", "content": prompt}],
                    ),
                    timeout=120,
                )
                return response.content[0].text
            elif self.provider in ("openai", "deepseek"):
                if self.openai_client is None:
                    raise ValueError(f"OpenAI-compatible client not initialized for '{self.provider}'")
                messages = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                messages.append({"role": "user", "content": prompt})
                response = await asyncio.wait_for(
                    self.openai_client.chat.completions.create(
                        model=model_name,
                        messages=messages,
                    ),
                    timeout=120,
                )
                return response.choices[0].message.content
            else:
                raise NotImplementedError(f"Provider {self.provider} not implemented")
        except asyncio.TimeoutError:
            logging.error(f"Timeout for {self.provider}/{model_name}")
            raise Exception(f"Timeout connecting to {self.provider}")
        except Exception as e:
            logging.error(f"Error during {self.provider}/{model_name} generation: {e}")
            raise


def _get_available_providers() -> list:
    available = []
    for prov in ("anthropic", "openai", "deepseek"):
        key_env = _PROVIDER_API_KEY_ENV.get(prov, "")
        if os.environ.get(key_env):
            available.append(prov)
    if not available:
        available = ["anthropic", "openai", "deepseek"]
    return available


def _create_unified_llm() -> UnifiedLLM:
    unified = UnifiedLLM()

    available = _get_available_providers()
    unified.set_available_providers(available)

    adapters: Dict[str, LLMAdapter] = {}
    for prov in available:
        adapters[prov] = LLMAdapter(provider=prov)

    async def _generate_fn(provider: str, model: str, prompt: str, system_prompt: str) -> str:
        adapter = adapters.get(provider)
        if adapter is None:
            return f"[UNKNOWN_PROVIDER] {provider}"
        return await adapter.generate(prompt, system_prompt, model=model)

    unified.set_generate_fn(_generate_fn)
    return unified


def create_unified_llm() -> UnifiedLLM:
    unified = _create_unified_llm()
    unified.validate_startup()
    return unified


class ConfigurableHeadProvider(HeadProvider):
    def __init__(self, unified: UnifiedLLM = None):
        self.unified = unified or create_unified_llm()
        self.compiler = PromptCompiler()

    async def execute(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        blackboard_entries = context.get("blackboard_entries", [])
        system_prompt = self.compiler.compile_head_system_prompt(blackboard_entries)
        response = await self.unified.generate(
            prompt=task.description,
            system_prompt=system_prompt,
            mode="quality",
        )
        return {
            "tier": "head",
            "status": "success",
            "output": response.content,
            "provider": response.provider_used,
            "model": response.model,
            "latency_ms": response.latency_ms,
            "cost_estimate_usd": response.cost_estimate_usd,
            "dry_run": response.dry_run,
            "fallback_used": response.fallback_used,
        }


class ConfigurableMiddleProvider(MiddleProvider):
    def __init__(self, unified: UnifiedLLM = None):
        self.unified = unified or create_unified_llm()

    async def execute(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        response = await self.unified.generate(
            prompt=task.description,
            mode="balanced",
        )
        return {
            "tier": "middle",
            "status": "success",
            "output": response.content,
            "provider": response.provider_used,
            "model": response.model,
            "latency_ms": response.latency_ms,
            "cost_estimate_usd": response.cost_estimate_usd,
            "dry_run": response.dry_run,
            "fallback_used": response.fallback_used,
        }


class ConfigurableWorkerProvider(WorkerProvider):
    def __init__(self, unified: UnifiedLLM = None):
        self.unified = unified or create_unified_llm()

    async def execute(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        response = await self.unified.generate(
            prompt=task.description,
            mode="cheap",
        )
        return {
            "tier": "worker",
            "status": "success",
            "output": response.content,
            "provider": response.provider_used,
            "model": response.model,
            "latency_ms": response.latency_ms,
            "cost_estimate_usd": response.cost_estimate_usd,
            "dry_run": response.dry_run,
            "fallback_used": response.fallback_used,
        }
