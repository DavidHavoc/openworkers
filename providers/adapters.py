import os
import asyncio
from typing import Dict, Any
import logging

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from core.schemas import Task
from providers.interfaces import HeadProvider, MiddleProvider, WorkerProvider
from core.orchestrator.compiler import PromptCompiler

class LLMAdapter:
    """Generic adapter that can swap between real LLM API calls and dry_run simulation."""
    def __init__(self, tier: str):
        self.tier = tier
        # if REAL_API is disabled, it acts like the mock
        self.dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"
        self.anthropic_client = None
        self.openai_client = None
        
        # Route logic to map tiers to API endpoints
        if self.tier == "head":
            self.model_provider = "anthropic" # e.g. Claude 3 Opus
            self.api_key = os.environ.get("ANTHROPIC_API_KEY")
            self.model_name = "claude-3-opus-20240229"
            if not self.dry_run and self.api_key:
                self.anthropic_client = AsyncAnthropic(api_key=self.api_key)
        elif self.tier == "middle":
            self.model_provider = "openai" # e.g. GPT-4o-mini
            self.api_key = os.environ.get("OPENAI_API_KEY")
            self.model_name = "gpt-4o-mini"
            if not self.dry_run and self.api_key:
                self.openai_client = AsyncOpenAI(api_key=self.api_key)
        elif self.tier == "worker":
            self.model_provider = "deepseek" # e.g. DeepSeek Coder/Chat
            self.api_key = os.environ.get("DEEPSEEK_API_KEY")
            self.base_url = "https://api.deepseek.com"
            self.model_name = "deepseek-chat"
            if not self.dry_run and self.api_key:
                self.openai_client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        else:
            self.model_provider = "unknown"
            self.api_key = None

    async def generate(self, prompt: str, system_prompt: str = "") -> str:
        if self.dry_run:
            await asyncio.sleep(0.01) # simulated latency
            return f"[{self.tier.upper()} DRY_RUN via {self.model_provider.upper()}] Processed: {prompt[:50]}..."
        else:
            if not self.api_key:
                raise ValueError(f"No API key configured for tier '{self.tier}' using '{self.model_provider}'")
            
            try:
                if self.model_provider == "anthropic":
                    response = await asyncio.wait_for(
                        self.anthropic_client.messages.create(
                            model=self.model_name,
                            max_tokens=4096,
                            system=system_prompt,
                            messages=[{"role": "user", "content": prompt}]
                        ),
                        timeout=120
                    )
                    return response.content[0].text
                elif self.model_provider in ["openai", "deepseek"]:
                    messages = []
                    if system_prompt:
                        messages.append({"role": "system", "content": system_prompt})
                    messages.append({"role": "user", "content": prompt})
                    
                    response = await asyncio.wait_for(
                        self.openai_client.chat.completions.create(
                            model=self.model_name,
                            messages=messages
                        ),
                        timeout=120
                    )
                    return response.choices[0].message.content
                else:
                    raise NotImplementedError(f"Provider {self.model_provider} not implemented.")
            except asyncio.TimeoutError:
                logging.error(f"Timeout during generation for tier {self.tier}")
                raise Exception(f"Timeout connecting to {self.model_provider}")
            except Exception as e:
                logging.error(f"Error during generation for tier {self.tier}: {e}")
                raise


class ConfigurableHeadProvider(HeadProvider):
    def __init__(self):
        self.adapter = LLMAdapter(tier="head")
        self.compiler = PromptCompiler()

    async def execute(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        blackboard_entries = context.get("blackboard_entries", [])
        system_prompt = self.compiler.compile_head_system_prompt(blackboard_entries)
        
        output = await self.adapter.generate(prompt=task.description, system_prompt=system_prompt)
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
        output = await self.adapter.generate(prompt=task.description)
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
        output = await self.adapter.generate(prompt=task.description)
        return {
            "tier": "worker",
            "status": "success",
            "output": output,
            "dry_run": self.adapter.dry_run
        }
