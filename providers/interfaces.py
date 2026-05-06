from abc import abstractmethod
from typing import Any, Dict

from core.schemas import Task
from providers.base import BaseAgentProvider


class HeadProvider(BaseAgentProvider):
    @abstractmethod
    async def execute(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        pass

class MiddleProvider(BaseAgentProvider):
    @abstractmethod
    async def execute(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        pass

class WorkerProvider(BaseAgentProvider):
    @abstractmethod
    async def execute(self, task: Task, context: Dict[str, Any]) -> Dict[str, Any]:
        pass
