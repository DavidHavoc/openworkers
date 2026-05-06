import time
import uuid
from datetime import datetime
from typing import Any, Dict

from core.blackboard.engine import Blackboard
from core.memory.episodic import EpisodicMemory
from core.observability.metrics import obs_logger
from core.router.engine import Router
from core.schemas import (
    BudgetState,
    EpisodeMetrics,
    EpisodeModels,
    EpisodeQuality,
    EpisodeRoute,
    MemoryEpisode,
    SessionState,
    Task,
    UserRequest,
)
from providers.adapters import (
    ConfigurableHeadProvider,
    ConfigurableMiddleProvider,
    ConfigurableWorkerProvider,
)


class TaskOrchestrator:
    def __init__(self, memory: EpisodicMemory, router: Router):
        self.memory = memory
        self.router = router
        self.blackboard = Blackboard()

        self.head_provider = ConfigurableHeadProvider()
        self.middle_provider = ConfigurableMiddleProvider()
        self.worker_provider = ConfigurableWorkerProvider()

    async def execute_task(
        self, request: UserRequest, privacy_tier: str = "sanitized"
    ) -> Dict[str, Any]:
        start_time = time.time()

        session_id = request.session_id or str(uuid.uuid4())
        session = SessionState(
            session_id=session_id, status="running", created_at=datetime.utcnow().isoformat() + "Z"
        )

        task_id = str(uuid.uuid4())
        task = Task(
            task_id=task_id,
            description=request.query,
            complexity_estimated="medium",
            status="running",
        )

        self.blackboard.add_entry("task", task.model_dump())
        obs_logger.log_event(
            "task_started", session_id, {"task_id": task_id, "privacy": privacy_tier}
        )

        budget = BudgetState(remaining_usd=1.00, spent_usd=0.0, token_limit=100000)

        # Memory retrieval
        memory_brief = self.memory.retrieve_guidance(task.description, task_type="general")
        obs_logger.log_memory_hit(session_id, "general", memory_brief.similar_past_tasks_count)

        # Route logic
        route_decision = self.router.route_task(
            task=task,
            privacy_tier=privacy_tier,
            budget=budget,
            memory_brief=memory_brief,
            disagreement_risk="low",
            needs_tools=False,
        )
        self.blackboard.add_entry("route_decision", route_decision.model_dump())

        # Execution
        outputs = []
        try:
            if route_decision.workers_allowed:
                worker_out = await self.worker_provider.execute(
                    task, self.blackboard.get_all_entries()
                )
                self.blackboard.add_entry("agent_output", worker_out)
                outputs.append(worker_out)

                if route_decision.middle_allowed:
                    middle_out = await self.middle_provider.execute(
                        task, self.blackboard.get_all_entries()
                    )
                    self.blackboard.add_entry("agent_output", middle_out)
                    outputs.append(middle_out)

            head_out = await self.head_provider.execute(task, self.blackboard.get_all_entries())
            self.blackboard.add_entry("agent_output", head_out)
            outputs.append(head_out)
            success = True
        except Exception as e:
            obs_logger.log_event("execution_failed", session_id, {"error": str(e)})
            success = False
            outputs.append({"error": str(e)})

        elapsed_ms = int((time.time() - start_time) * 1000)
        obs_logger.log_trace(session_id, route_decision.strategy, elapsed_ms, success)
        obs_logger.log_budget(session_id, "routing_cost_usd", 0.01)

        # Final episodic record
        episode = MemoryEpisode(
            episode_id=str(uuid.uuid4()),
            timestamp=datetime.utcnow().isoformat() + "Z",
            task_summary=task.description,
            task_type="general",
            privacy_tier=privacy_tier,
            route=EpisodeRoute(
                head_direct=route_decision.head_direct,
                used_middle_tier=route_decision.middle_allowed,
                used_worker_swarm=route_decision.workers_allowed,
                spawn_count=len(outputs),
            ),
            models=EpisodeModels(
                head="configurable_head",
                workers=["configurable_worker"] if route_decision.workers_allowed else [],
            ),
            metrics=EpisodeMetrics(latency_ms=elapsed_ms, estimated_cost_usd=0.01),
            quality=EpisodeQuality(score=0.9 if success else 0.0, accepted=success, confidence=0.8),
        )
        self.memory.store_episode(episode)

        return {
            "session_id": session.session_id,
            "task_id": task.task_id,
            "route_strategy": route_decision.strategy,
            "outputs": outputs,
            "memory_brief": memory_brief.to_formatted_string(),
        }
