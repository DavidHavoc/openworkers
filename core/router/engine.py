import os
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

from core.schemas import RouteDecision, Task, MemoryBrief, BudgetState, BlackboardEntry


@dataclass
class ThesisRoute:
    phase: str
    activate_head_planner: bool = False
    activate_head_supervisor: bool = False
    activate_researcher: bool = False
    activate_checker: bool = False
    activate_synthesizer: bool = False
    activate_critic: bool = False
    reason: str = ""
    provider_map: Dict[str, tuple[str, str]] = field(default_factory=dict)

    def agents_to_run(self) -> List[str]:
        agents: List[str] = []
        if self.activate_head_planner:
            agents.append("head_planner")
        if self.activate_head_supervisor:
            agents.append("head_supervisor")
        if self.activate_researcher:
            agents.append("researcher")
        if self.activate_checker:
            agents.append("checker")
        if self.activate_synthesizer:
            agents.append("synthesizer")
        if self.activate_critic:
            agents.append("critic")
        return agents


def _read_provider_for_mode(mode: str) -> tuple[str, str]:
    key = mode.upper()
    provider = os.environ.get(f"THESIS_{key}_PROVIDER", "").strip().lower()
    model = os.environ.get(f"THESIS_{key}_MODEL", "").strip()
    if not model and provider:
        model = "unknown"
    return provider, model


class Router:
    def __init__(self):
        pass

    def route_task(self,
                   task: Task,
                   privacy_tier: str,
                   budget: BudgetState,
                   memory_brief: MemoryBrief,
                   disagreement_risk: str = "low",
                   needs_tools: bool = False) -> RouteDecision:
        if privacy_tier == "trusted":
            return RouteDecision(
                strategy="head_direct",
                head_direct=True,
                workers_allowed=False,
                middle_allowed=False,
                rationale="Privacy constraint: 'trusted' requires direct HEAD resolution without exposure to workers."
            )

        if budget.remaining_usd < 0.05:
            return RouteDecision(
                strategy="head_direct",
                head_direct=True,
                workers_allowed=False,
                middle_allowed=False,
                rationale="Low budget requires cheapest resolution path."
            )

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

        memory_bias = memory_brief.recommended_routing_bias
        rationale = f"Routing defaulted to head_direct. Memory guidance: {memory_bias}" if memory_bias else "Default simple routing."
        return RouteDecision(
            strategy="head_direct",
            head_direct=True,
            workers_allowed=False,
            middle_allowed=False,
            rationale=rationale
        )

    def route_thesis_task(
        self,
        phase: str = "full",
        privacy_tier: str = "public",
        budget: Optional[BudgetState] = None,
        research_plan: Optional[Any] = None,
    ) -> ThesisRoute:
        reasons: List[str] = []

        if privacy_tier == "trusted":
            return ThesisRoute(
                phase=phase,
                activate_head_planner=True,
                activate_head_supervisor=True,
                reason="Privacy tier 'trusted': head only, no external API calls.",
                provider_map=self._build_provider_map(),
            )

        route = ThesisRoute(phase=phase)

        budget_tight = budget is not None and budget.remaining_usd < 0.05

        if phase == "full":
            route.activate_head_planner = True
            route.activate_researcher = True
            route.activate_checker = True
            route.activate_synthesizer = not budget_tight
            route.activate_critic = True
            route.activate_head_supervisor = True
            reasons.append("full pipeline: all agents")

            if budget_tight:
                reasons.append("budget tight: synthesizer skipped")
                if route.activate_synthesizer:
                    route.activate_synthesizer = False

        elif phase == "search_only":
            route.activate_head_planner = True
            route.activate_researcher = True
            reasons.append("search_only: head_planner + researcher")

        elif phase == "verify":
            route.activate_checker = True
            reasons.append("verify: checker only")

        elif phase == "critique_only":
            route.activate_head_planner = True
            route.activate_critic = True
            reasons.append("critique_only: head_planner + critic")

        else:
            route.activate_head_planner = True
            route.activate_head_supervisor = True
            reasons.append(f"unknown phase '{phase}': defaulted to head only")

        route.reason = " | ".join(reasons)
        route.provider_map = self._build_provider_map()

        return route

    def _build_provider_map(self) -> Dict[str, tuple[str, str]]:
        provider_map: Dict[str, tuple[str, str]] = {}
        for mode in ("quality", "balanced", "cheap"):
            p, m = _read_provider_for_mode(mode)
            if p and m:
                provider_map[mode] = (p, m)
        return provider_map
