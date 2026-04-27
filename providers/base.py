from abc import ABC, abstractmethod
from typing import Any, Dict

class BaseAgentProvider(ABC):
    """Abstract interface for all tiers of agents."""
    
    @abstractmethod
    async def execute(self, task: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the task given the blackboard context."""
        pass
