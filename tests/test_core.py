import pytest
import fakeredis
from core.schemas import UserRequest, Task, BudgetState, MemoryBrief
from core.blackboard.engine import Blackboard
from core.router.engine import Router
from core.memory.episodic import EpisodicMemory
from core.orchestrator.flow import TaskOrchestrator

@pytest.fixture(autouse=True)
def mock_redis(monkeypatch):
    server = fakeredis.FakeServer()
    # Mock redis.from_url to return a FakeRedis instance
    monkeypatch.setattr("redis.from_url", lambda *args, **kwargs: fakeredis.FakeRedis(server=server, decode_responses=True))

def test_schema_validation():
    # Valid
    req = UserRequest(query="test", priority=2)
    assert req.query == "test"
    assert req.priority == 2

    # Invalid: missing required query
    with pytest.raises(ValueError):
        UserRequest(priority=2)

def test_blackboard_validation():
    bb = Blackboard()
    # Valid added
    bb.add_entry("task", {"desc": "test task"})
    
    # Invalid entry type
    with pytest.raises(ValueError):
        bb.add_entry("invalid_type", {"data": "test"})

def test_privacy_blocking_routing():
    router = Router()
    task = Task(task_id="1", description="test", complexity_estimated="high")
    budget = BudgetState(remaining_usd=10.0, spent_usd=0.0, token_limit=1000)
    memory_brief = MemoryBrief()

    # Trusted privacy tier should override high complexity to head_direct
    decision = router.route_task(task, "trusted", budget, memory_brief)
    assert decision.strategy == "head_direct"
    assert decision.head_direct is True
    assert decision.workers_allowed is False

def test_heuristic_routing_complexity():
    router = Router()
    task = Task(task_id="1", description="test", complexity_estimated="high")
    budget = BudgetState(remaining_usd=10.0, spent_usd=0.0, token_limit=1000)
    memory_brief = MemoryBrief()

    decision = router.route_task(task, "public", budget, memory_brief)
    assert decision.strategy == "head_middle_workers"
    assert decision.workers_allowed is True
    assert decision.middle_allowed is True

def test_memory_retrieval():
    memory = EpisodicMemory(qdrant_location=":memory:")
    from core.schemas import MemoryEpisode, EpisodeRoute, EpisodeModels, EpisodeMetrics, EpisodeQuality
    # Store a mock dummy
    memory.store_episode(MemoryEpisode(
        episode_id="11111111-1111-1111-1111-111111111111",
        timestamp="2026-04-26",
        task_summary="test task",
        task_type="test_type",
        privacy_tier="public",
        route=EpisodeRoute(head_direct=True),
        models=EpisodeModels(),
        metrics=EpisodeMetrics(),
        quality=EpisodeQuality(accepted=True)
    ))
    
    brief = memory.retrieve_guidance("test task", "test_type")
    assert brief.similar_past_tasks_count == 1
    assert "was the most consistently successful pattern" in brief.strongest_successful_pattern
    assert "was the cheapest successful path at" in brief.cheapest_acceptable_pattern

@pytest.mark.asyncio
async def test_orchestrator_happy_path(monkeypatch):
    memory = EpisodicMemory(qdrant_location=":memory:")
    router = Router()
    orchestrator = TaskOrchestrator(memory, router)
    
    request = UserRequest(query="Do some research on deep learning.")
    result = await orchestrator.execute_task(request)
    
    assert "session_id" in result
    assert "task_id" in result
    # For medium task, it should use 'head_workers' based on routing heuristic
    assert result["route_strategy"] == "head_workers"
    # Outputs should include worker and head
    assert len(result["outputs"]) == 2
    
    # Check if memory stored the new episode
    brief = memory.retrieve_guidance("Do some research on deep learning.")
    assert brief.similar_past_tasks_count >= 1
