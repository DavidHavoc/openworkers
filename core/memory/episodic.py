import os
from typing import Optional

from qdrant_client import QdrantClient

from core.schemas import MemoryBrief, MemoryEpisode


class EpisodicMemory:
    """
    Manages long-term storage and retrieval of routing episodes for heuristic induction.
    Uses Qdrant vector database for semantic retrieval via FastEmbed.
    """

    def __init__(self, qdrant_location: Optional[str] = None):
        qdrant_url = os.environ.get("QDRANT_URL")
        if qdrant_url:
            self.client = QdrantClient(url=qdrant_url)
        elif qdrant_location:
            self.client = QdrantClient(location=qdrant_location)
        else:
            self.client = QdrantClient(path="./qdrant_data")

        self.collection_name = "episodes"

        # fastembed model
        self.client.set_model("BAAI/bge-small-en-v1.5")

        if not self.client.collection_exists(collection_name=self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=self.client.get_fastembed_vector_params(),
            )

    def store_episode(self, episode: MemoryEpisode):
        """Stores a compact routing episode into Qdrant."""
        self.client.add(
            collection_name=self.collection_name,
            documents=[episode.task_summary],
            metadata=[episode.model_dump(exclude={"task_summary"})],
            ids=[episode.episode_id],
        )

    def retrieve_guidance(self, task: str, task_type: str = "general") -> MemoryBrief:
        """
        Retrieves semantically similar past tasks and synthesizes a MemoryBrief.
        """
        # Search Qdrant
        search_results = self.client.query(
            collection_name=self.collection_name, query_text=task, limit=5
        )

        # In Qdrant, results are returned as QueryResponse objects
        relevant_episodes = []
        for result in search_results:
            # Reconstruct the episode from metadata
            # qdrant 'add' method sets metadata in 'payload'
            if result.score > 0.8:  # Filter out low similarity
                meta = result.metadata
                meta["task_summary"] = result.document
                relevant_episodes.append(MemoryEpisode.model_validate(meta))

        brief = MemoryBrief(similar_past_tasks_count=len(relevant_episodes))

        if len(relevant_episodes) == 0:
            return brief

        # Synthesize takeaways based on history
        successful = [e for e in relevant_episodes if e.quality.accepted]
        failures = [e for e in relevant_episodes if not e.quality.accepted]

        brief.confidence = "medium" if len(relevant_episodes) > 3 else "low"

        if successful:
            # Dynamically assess the most common successful route
            route_counts = {}
            for e in successful:
                route_str = str(e.route.model_dump())
                route_counts[route_str] = route_counts.get(route_str, 0) + 1
            most_common = max(route_counts, key=route_counts.get)

            brief.strongest_successful_pattern = (
                f"Route {most_common} was the most consistently successful pattern."
            )

            cheapest = sorted(successful, key=lambda x: x.metrics.estimated_cost_usd)[0]
            brief.cheapest_acceptable_pattern = f"Route {cheapest.route.model_dump()} was the cheapest successful path at ${cheapest.metrics.estimated_cost_usd:.4f}."

            fastest = sorted(successful, key=lambda x: x.metrics.latency_ms)[0]
            brief.fastest_acceptable_pattern = f"Route {fastest.route.model_dump()} yielded fastest latency ({fastest.metrics.latency_ms}ms)."

            if cheapest.route == fastest.route:
                brief.recommended_routing_bias = f"Lean towards route: {cheapest.route.model_dump()} as it optimizes both cost and speed."
            else:
                brief.recommended_routing_bias = f"Weigh tradeoffs between cheapest ({cheapest.route.model_dump()}) and fastest ({fastest.route.model_dump()})."
        else:
            brief.recommended_routing_bias = "Try worker swarm to gather more information."

        if failures:
            brief.common_failure_mode = (
                failures[-1].failures[0] if failures[-1].failures else "Model drifted off-topic."
            )

        return brief
