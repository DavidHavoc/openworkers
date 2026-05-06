import fakeredis
import pytest

from core.blackboard.engine import Blackboard
from core.memory.episodic import EpisodicMemory
from core.orchestrator.flow import TaskOrchestrator
from core.router.engine import Router
from core.schemas import BudgetState, MemoryBrief, Task, UserRequest


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


def test_router_provider_map_defaults(monkeypatch):
    """provider_map has all three modes with sensible defaults when env vars are unset."""
    monkeypatch.delenv("THESIS_QUALITY_PROVIDER", raising=False)
    monkeypatch.delenv("THESIS_QUALITY_MODEL", raising=False)
    monkeypatch.delenv("THESIS_BALANCED_PROVIDER", raising=False)
    monkeypatch.delenv("THESIS_BALANCED_MODEL", raising=False)
    monkeypatch.delenv("THESIS_CHEAP_PROVIDER", raising=False)
    monkeypatch.delenv("THESIS_CHEAP_MODEL", raising=False)

    router = Router()
    route = router.route_thesis_task(phase="full")

    assert set(route.provider_map.keys()) == {"quality", "balanced", "cheap"}
    assert route.provider_map["quality"] == ("anthropic", "claude-sonnet-4-20250514")
    assert route.provider_map["balanced"] == ("openai", "gpt-4o-mini")
    assert route.provider_map["cheap"] == ("deepseek", "deepseek-chat")


def test_router_provider_map_env_vars(monkeypatch):
    """provider_map reflects custom env var overrides per mode."""
    monkeypatch.setenv("THESIS_QUALITY_PROVIDER", "anthropic")
    monkeypatch.setenv("THESIS_QUALITY_MODEL", "claude-opus-4")
    monkeypatch.setenv("THESIS_BALANCED_PROVIDER", "openai")
    monkeypatch.setenv("THESIS_BALANCED_MODEL", "gpt-4o")
    monkeypatch.setenv("THESIS_CHEAP_PROVIDER", "deepseek")
    monkeypatch.setenv("THESIS_CHEAP_MODEL", "deepseek-v3")

    router = Router()
    route = router.route_thesis_task(phase="full")

    assert route.provider_map["quality"] == ("anthropic", "claude-opus-4")
    assert route.provider_map["balanced"] == ("openai", "gpt-4o")
    assert route.provider_map["cheap"] == ("deepseek", "deepseek-v3")


def test_router_provider_map_partial_env(monkeypatch):
    """Modes without env vars fall back to defaults; modes with env vars use them."""
    monkeypatch.delenv("THESIS_QUALITY_PROVIDER", raising=False)
    monkeypatch.delenv("THESIS_QUALITY_MODEL", raising=False)
    monkeypatch.setenv("THESIS_CHEAP_PROVIDER", "openai")
    monkeypatch.setenv("THESIS_CHEAP_MODEL", "gpt-4o-mini")

    router = Router()
    route = router.route_thesis_task(phase="full")

    assert route.provider_map["quality"] == ("anthropic", "claude-sonnet-4-20250514")
    assert route.provider_map["cheap"] == ("openai", "gpt-4o-mini")


def test_router_fallback_chain(monkeypatch):
    """provider_fallback lists the preferred provider first, then all others."""
    monkeypatch.delenv("THESIS_QUALITY_PROVIDER", raising=False)
    monkeypatch.delenv("THESIS_QUALITY_MODEL", raising=False)

    router = Router()
    route = router.route_thesis_task(phase="full")

    # Default preferred is anthropic → [anthropic, openai, deepseek]
    assert route.provider_fallback["quality"] == ["anthropic", "openai", "deepseek"]
    assert route.provider_fallback["balanced"] == ["openai", "anthropic", "deepseek"]
    assert route.provider_fallback["cheap"] == ["deepseek", "anthropic", "openai"]


def test_router_fallback_unavailable_preferred(monkeypatch):
    """Fallback chain includes all known providers even when preferred is unrecognized."""
    monkeypatch.setenv("THESIS_QUALITY_PROVIDER", "nonexistent")
    monkeypatch.setenv("THESIS_QUALITY_MODEL", "v1")

    router = Router()
    route = router.route_thesis_task(phase="full")

    assert route.provider_map["quality"] == ("nonexistent", "v1")
    assert route.provider_fallback["quality"] == ["anthropic", "openai", "deepseek"]


def test_router_privacy_trusted_forces_head_only(monkeypatch):
    """Privacy tier 'trusted' activates only head agents regardless of phase."""
    router = Router()
    route = router.route_thesis_task(phase="full", privacy_tier="trusted")

    assert route.activate_head_planner is True
    assert route.activate_head_supervisor is True
    assert route.activate_researcher is False
    assert route.activate_checker is False
    assert route.activate_synthesizer is False
    assert route.activate_critic is False
    assert "trusted" in route.reason.lower()
    assert len(route.provider_map) == 3


def test_memory_retrieval():
    memory = EpisodicMemory(qdrant_location=":memory:")
    from core.schemas import (
        EpisodeMetrics,
        EpisodeModels,
        EpisodeQuality,
        EpisodeRoute,
        MemoryEpisode,
    )
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


def test_thesis_get_entries_delegates_to_blackboard():
    """_get_entries() returns blackboard entries without infinite recursion."""
    from core.memory.episodic import EpisodicMemory
    from core.orchestrator.thesis_flow import ThesisOrchestrator
    from core.router.engine import Router
    from providers.unified import UnifiedLLM

    bb = Blackboard()
    bb.add_entry("task", {"desc": "a"})
    bb.add_entry("status", {"stage": "init"})

    unified = UnifiedLLM()
    memory = EpisodicMemory(qdrant_location=":memory:")
    router = Router()

    orch = ThesisOrchestrator(unified=unified, memory=memory, router=router, blackboard=bb)
    entries = orch._get_entries()

    assert len(entries) == 2
    assert entries[0].entry_type == "task"
    assert entries[1].entry_type == "status"


@pytest.mark.asyncio
async def test_thesis_pipeline_dry_run_completes(monkeypatch):
    """Full 8-stage thesis pipeline completes in DRY_RUN mode without fatal errors."""
    from core.memory.episodic import EpisodicMemory
    from core.orchestrator.thesis_flow import ThesisOrchestrator
    from core.router.engine import Router
    from core.schemas import ResearchContext
    from providers.unified import UnifiedLLM
    from tools.mcp.engine import ToolRegistry

    monkeypatch.setenv("DRY_RUN", "true")

    unified = UnifiedLLM()
    memory = EpisodicMemory(qdrant_location=":memory:")
    router = Router()
    tools = ToolRegistry()

    orch = ThesisOrchestrator(
        unified=unified,
        memory=memory,
        router=router,
        tool_registry=tools,
    )

    rc = ResearchContext(
        research_question="Does caffeine improve proofreading accuracy?",
        topic_summary="A meta-analysis of caffeine and attention-to-detail tasks.",
        discipline="psychology",
    )

    session = await orch.execute(rc)

    assert session.status in ("complete", "partial")
    assert session.session_id
    assert session.research_context == rc
    assert session.research_plan is not None
    assert session.lit_map is not None
    assert session.citation_audit is not None
    assert session.synthesis_report is not None
    assert session.critique is not None
