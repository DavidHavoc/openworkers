import os
import asyncio
from typing import Dict, Any
from core.schemas import Task
from providers.interfaces import HeadProvider, MiddleProvider, WorkerProvider

class LLMAdapter:
    """Generic adapter that can swap between real LLM API calls and dry_run simulation."""
    def __init__(self, tier: str):
        self.tier = tier
        # if REAL_API is disabled, it acts like the mock
        self.dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"
        
        # Route logic to map tiers to API endpoints
        if self.tier == "head":
            self.model_provider = "anthropic" # e.g. Claude 3 Opus
            self.api_key = os.environ.get("ANTHROPIC_API_KEY")
        elif self.tier == "middle":
            self.model_provider = "openai" # e.g. GPT-4o-mini
            self.api_key = os.environ.get("OPENAI_API_KEY")
        elif self.tier == "worker":
            self.model_provider = "deepseek" # e.g. DeepSeek Coder/Chat
            self.api_key = os.environ.get("DEEPSEEK_API_KEY")
        else:
            self.model_provider = "unknown"
            self.api_key = None

    async def generate(self, prompt: str) -> str:
        if self.dry_run:
            await asyncio.sleep(0.01) # simulated latency
            return f"[{self.tier.upper()} DRY_RUN via {self.model_provider.upper()}] Processed: {prompt[:50]}..."
        else:
            # Placeholder for actual Anthropic/DeepSeek/OpenAI integrations
            if not self.api_key:
                raise ValueError(f"No API key configured for tier '{self.tier}' using '{self.model_provider}'")
            raise NotImplementedError(f"Real {self.model_provider} API endpoint not configured yet.")


class ConfigurableHeadProvider(HeadProvider):
    def __init__(self):
        self.adapter = LLMAdapter(tier="head")

    async def execute(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        output = await self.adapter.generate(task.description)
        return {
            "tier": "head",
            "status": "success",
            "output": output,
            "dry_run": self.adapter.dry_run
        }

class ConfigurableMiddleProvider(MiddleProvider):
    def __init__(self):
        self.adapter = LLMAdapter(tier="middle")

    async def execute(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        output = await self.adapter.generate(task.description)
        return {
            "tier": "middle",
            "status": "success",
            "output": output,
            "dry_run": self.adapter.dry_run
        }

class ConfigurableWorkerProvider(WorkerProvider):
    def __init__(self):
        self.adapter = LLMAdapter(tier="worker")

    async def execute(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        output = await self.adapter.generate(task.description)
        return {
            "tier": "worker",
            "status": "success",
            "output": output,
            "dry_run": self.adapter.dry_run
        }
