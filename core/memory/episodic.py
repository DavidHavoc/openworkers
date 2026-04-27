from typing import List, Optional, Dict, Any
from core.schemas import MemoryEpisode, MemoryBrief

class EpisodicMemory:
    """
    Manages storage and retrieval of routing episodes for heuristic induction.
    Separates raw episodes from generalized heuristics (MemoryBrief).
    """
    def __init__(self):
        # In a real implementation this would be backed by a vector DB
        self._history: List[MemoryEpisode] = []

    def store_episode(self, episode: MemoryEpisode):
        """Stores a compact routing episode."""
        self._history.append(episode)

    def retrieve_guidance(self, task: str, task_type: str) -> MemoryBrief:
        """
        Retrieves similar past tasks and synthesizes a MemoryBrief.
        In this v1, uses dummy logic to simulate DB retrieval and LLM synthesis.
        """
        # Filter matching tasks conceptually. Here just a mock logic.
        relevant_episodes = [e for e in self._history if e.task_type == task_type]
        
        brief = MemoryBrief(
            similar_past_tasks_count=len(relevant_episodes)
        )
        
        if len(relevant_episodes) == 0:
            return brief
            
        # Simulate generating takeaways based on history
        successful = [e for e in relevant_episodes if e.quality.accepted]
        failures = [e for e in relevant_episodes if not e.quality.accepted]
        
        brief.confidence = "medium" if len(relevant_episodes) > 3 else "low"
        
        if successful:
            brief.strongest_successful_pattern = "Using map-reduce works well for this task type."
            brief.cheapest_acceptable_pattern = "Direct HEAD resolution is cheapest if complexity is low."
            fastest = sorted(successful, key=lambda x: x.metrics.latency_ms)[0]
            brief.fastest_acceptable_pattern = f"Route {fastest.route.model_dump()} yielded fastest latency."
            brief.recommended_routing_bias = "Lean towards head_direct with tool access."
        else:
            brief.recommended_routing_bias = "Try worker swarm to gather more information."
            
        if failures:
            brief.common_failure_mode = failures[-1].failures[0] if failures[-1].failures else "Model drifted off-topic."

        return brief
