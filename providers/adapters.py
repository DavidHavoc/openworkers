import os
import json
import asyncio
from typing import Dict, Any, Optional
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

    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        model: str = None,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> str:
        model_name = model or self.default_model

        if self.dry_run:
            await asyncio.sleep(0.01)
            if response_schema:
                return _generate_placeholder_json(response_schema)
            return f"[{self.provider.upper()}/{model_name} DRY_RUN] Processed: {prompt[:50]}..."
        if not self.api_key:
            raise ValueError(f"No API key configured for provider '{self.provider}'")
        try:
            if self.provider == "anthropic":
                return await self._generate_anthropic(model_name, prompt, system_prompt, response_schema)
            elif self.provider == "openai":
                return await self._generate_openai(model_name, prompt, system_prompt, response_schema, strict_schema=True)
            elif self.provider == "deepseek":
                return await self._generate_openai(model_name, prompt, system_prompt, response_schema, strict_schema=False)
            else:
                raise NotImplementedError(f"Provider {self.provider} not implemented")
        except asyncio.TimeoutError:
            logging.error(f"Timeout for {self.provider}/{model_name}")
            raise Exception(f"Timeout connecting to {self.provider}")
        except Exception as e:
            logging.error(f"Error during {self.provider}/{model_name} generation: {e}")
            raise

    async def _generate_anthropic(
        self,
        model_name: str,
        prompt: str,
        system_prompt: str,
        response_schema: Optional[Dict[str, Any]],
    ) -> str:
        if self.anthropic_client is None:
            raise ValueError(f"Anthropic client not initialized for '{self.provider}'")

        kwargs: Dict[str, Any] = {
            "model": model_name,
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": [{"role": "user", "content": prompt}],
        }

        if response_schema:
            tool = {
                "name": "output",
                "description": "Produce a structured output that matches the requested schema.",
                "input_schema": response_schema,
            }
            kwargs["tools"] = [tool]
            kwargs["tool_choice"] = {"type": "tool", "name": "output"}

        response = await asyncio.wait_for(
            self.anthropic_client.messages.create(**kwargs),
            timeout=120,
        )

        if response_schema:
            for block in response.content:
                if block.type == "tool_use":
                    return json.dumps(block.input)
            raise ValueError("Anthropic did not return a tool_use block when schema was requested")

        return response.content[0].text

    async def _generate_openai(
        self,
        model_name: str,
        prompt: str,
        system_prompt: str,
        response_schema: Optional[Dict[str, Any]],
        strict_schema: bool = False,
    ) -> str:
        if self.openai_client is None:
            raise ValueError(f"OpenAI-compatible client not initialized for '{self.provider}'")

        messages: list = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        kwargs: Dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "timeout": 120,
        }

        if response_schema:
            if strict_schema:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "output",
                        "schema": response_schema,
                        "strict": True,
                    },
                }
            else:
                kwargs["response_format"] = {"type": "json_object"}

        response = await asyncio.wait_for(
            self.openai_client.chat.completions.create(**kwargs),
            timeout=120,
        )

        return response.choices[0].message.content


def _generate_placeholder_json(schema: Dict[str, Any]) -> str:
    props = schema.get("properties", {})
    required = schema.get("required", [])
    result: Dict[str, Any] = {}
    for key, prop in props.items():
        prop_type = prop.get("type", "string")
        if prop_type == "string":
            result[key] = "[DRY_RUN]" if key in required else ""
        elif prop_type == "integer" or prop_type == "number":
            result[key] = 0
        elif prop_type == "boolean":
            result[key] = False
        elif prop_type == "array":
            result[key] = []
        elif prop_type == "object":
            result[key] = {}
    return json.dumps(result)


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

    async def _generate_fn(provider: str, model: str, prompt: str, system_prompt: str, response_schema: Optional[Dict[str, Any]] = None) -> str:
        adapter = adapters.get(provider)
        if adapter is None:
            return f"[UNKNOWN_PROVIDER] {provider}"
        return await adapter.generate(prompt, system_prompt, model=model, response_schema=response_schema)

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
