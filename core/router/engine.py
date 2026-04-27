from core.schemas import RouteDecision, Task, MemoryBrief, BudgetState
from typing import Optional

class Router:
    """
    Deterministic heuristics router (v1).
    Reads episodic memory signals and constraints to compute the execution route.
    """
    
    def __init__(self):
        pass

    def route_task(self, 
                   task: Task, 
                   privacy_tier: str, 
                   budget: BudgetState, 
                   memory_brief: MemoryBrief, 
                   disagreement_risk: str = "low", 
                   needs_tools: bool = False) -> RouteDecision:
        """
        Determines the route based on privacy, task shape, budget, and memory guidance.
        """
        # Strict privacy overrides
        if privacy_tier == "trusted":
            return RouteDecision(
                strategy="head_direct",
                head_direct=True,
                workers_allowed=False,
                middle_allowed=False,
                rationale="Privacy constraint: 'trusted' requires direct HEAD resolution without exposure to workers."
            )

        # Budget constraints
        if budget.remaining_usd < 0.05:
            return RouteDecision(
                strategy="head_direct",
                head_direct=True,
                workers_allowed=False,
                middle_allowed=False,
                rationale="Low budget requires cheapest resolution path."
            )

        # Logic based on complexity and risk
        if task.complexity_estimated == "high" or disagreement_risk == "high":
            return RouteDecision(
                strategy="head_middle_workers",
                head_direct=False,
                workers_allowed=True,
                middle_allowed=True,
                rationale="High complexity or disagreement requires full swarm with middle-tier synthesis."
            )
            
        if needs_tools or task.complexity_estimated == "medium":
            return RouteDecision(
                strategy="head_workers",
                head_direct=False,
                workers_allowed=True,
                middle_allowed=False,
                rationale="Medium complexity or tool needs require worker swarm but no middle-tier synthesis."
            )

        # Default fallback (often guided by memory)
        memory_bias = memory_brief.recommended_routing_bias
        rationale = f"Routing defaulted to head_direct. Memory guidance: {memory_bias}" if memory_bias else "Default simple routing."
        return RouteDecision(
            strategy="head_direct",
            head_direct=True,
            workers_allowed=False,
            middle_allowed=False,
            rationale=rationale
        )
