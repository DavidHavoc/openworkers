import asyncio
from typing import Any, Dict
from providers.interfaces import HeadProvider, MiddleProvider, WorkerProvider
from core.schemas import Task

class MockHeadProvider(HeadProvider):
    async def execute(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        # Simulate network latency
        await asyncio.sleep(0.01)
        return {
            "tier": "head",
            "status": "success",
            "output": f"Head synthesized response for '{task.description}'"
        }

class MockMiddleProvider(MiddleProvider):
    async def execute(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        await asyncio.sleep(0.01)
        return {
            "tier": "middle",
            "status": "success",
            "output": "Middle tier grouped and ranked the worker outputs."
        }

class MockWorkerProvider(WorkerProvider):
    async def execute(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        await asyncio.sleep(0.01)
        return {
            "tier": "worker",
            "status": "success",
            "output": f"Worker discovered facts about '{task.description}'"
        }
