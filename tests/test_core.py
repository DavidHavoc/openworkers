import fakeredis
import fakeredis.aioredis
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
    monkeypatch.setattr(
        "redis.from_url",
        lambda *args, **kwargs: fakeredis.FakeRedis(server=server, decode_responses=True),
    )
    # Mock redis.asyncio.from_url to return an async FakeRedis instance
    monkeypatch.setattr(
        "redis.asyncio.from_url",
        lambda *args, **kwargs: fakeredis.aioredis.FakeRedis(server=server, decode_responses=True),
    )


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
    memory.store_episode(
        MemoryEpisode(
            episode_id="11111111-1111-1111-1111-111111111111",
            timestamp="2026-04-26",
            task_summary="test task",
            task_type="test_type",
            privacy_tier="public",
            route=EpisodeRoute(head_direct=True),
            models=EpisodeModels(),
            metrics=EpisodeMetrics(),
            quality=EpisodeQuality(accepted=True),
        )
    )

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


@pytest.mark.asyncio
async def test_session_store_save_and_load():
    """SessionStore.save() persists a session; load() retrieves it."""
    from core.schemas import ResearchContext, ResearchSession
    from core.sessions.store import RedisSessionStore

    store = RedisSessionStore()

    session = ResearchSession(
        session_id="test-session-001",
        research_context=ResearchContext(
            research_question="Test Q",
            topic_summary="Test summary",
            discipline="test",
        ),
        created_at="2026-01-01T00:00:00Z",
        status="complete",
    )
    await store.save(session)

    loaded = await store.load("test-session-001")
    assert loaded is not None
    assert loaded.session_id == "test-session-001"
    assert loaded.research_context.research_question == "Test Q"
    assert loaded.status == "complete"

    assert await store.load("nonexistent") is None


@pytest.mark.asyncio
async def test_session_store_list_and_count():
    """SessionStore.list_sessions() returns recent sessions; count() is accurate."""
    from core.schemas import ResearchContext, ResearchSession
    from core.sessions.store import RedisSessionStore

    store = RedisSessionStore()
    await store.clear_all()

    for i in range(3):
        session = ResearchSession(
            session_id=f"list-session-{i}",
            research_context=ResearchContext(
                research_question=f"Q{i}",
                topic_summary=f"Summary {i}",
                discipline="test",
            ),
            created_at="2026-01-01T00:00:00Z",
            status="complete",
        )
        await store.save(session)

    assert await store.count() == 3

    sessions = await store.list_sessions(limit=10)
    assert len(sessions) == 3
    for s in sessions:
        assert "session_id" in s
        assert "created_at" in s


@pytest.mark.asyncio
async def test_session_store_delete():
    """SessionStore.delete() removes a session and it's no longer loadable."""
    from core.schemas import ResearchContext, ResearchSession
    from core.sessions.store import RedisSessionStore

    store = RedisSessionStore()
    session = ResearchSession(
        session_id="delete-me",
        research_context=ResearchContext(
            research_question="Delete test",
            topic_summary="Delete summary",
            discipline="test",
        ),
        created_at="2026-01-01T00:00:00Z",
        status="complete",
    )
    await store.save(session)
    assert await store.load("delete-me") is not None

    deleted = await store.delete("delete-me")
    assert deleted is True
    assert await store.load("delete-me") is None
    assert await store.delete("nonexistent") is False


@pytest.mark.asyncio
async def test_search_literature_lanes_run_parallel(monkeypatch, tmp_path):
    """Literature search lanes are dispatched concurrently, not serially.

    Patches the inner lane fetcher to sleep, then asserts the wall-clock
    cost of N lanes is closer to 1×SLEEP than to N×SLEEP. Regression guard:
    a refactor that drops the asyncio.gather will trip this.
    """
    import asyncio
    import time as _time

    from core.memory.episodic import EpisodicMemory
    from core.orchestrator.thesis_flow import ThesisOrchestrator
    from core.router.engine import Router
    from core.schemas import ResearchPlan
    from providers.unified import UnifiedLLM
    from tools.mcp.engine import ToolRegistry

    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.chdir(tmp_path)

    SLEEP = 0.30

    orch = ThesisOrchestrator(
        unified=UnifiedLLM(),
        memory=EpisodicMemory(qdrant_location=":memory:"),
        router=Router(),
        tool_registry=ToolRegistry(),
    )

    async def _slow_lane(query, source="semantic_scholar", limit=10):
        await asyncio.sleep(SLEEP)
        return [{"paper_id": f"{source}:{query}", "source": source}]

    orch._search_literature_raw_inner = _slow_lane  # type: ignore[method-assign]

    plan = ResearchPlan(
        plan_id="p1",
        research_question="Q",
        scope="exploratory",
        deliverables=[],
        subquestions=[],
        search_lanes=[
            {"query": "a", "source": "arxiv"},
            {"query": "b", "source": "semantic_scholar"},
            {"query": "c", "source": "crossref"},
        ],
    )

    t0 = _time.perf_counter()
    results = await orch._search_literature_from_plan(plan, rag_collection=None, session_id="t")
    elapsed = _time.perf_counter() - t0

    assert len(results) == 3
    # Sequential bound: 3 * SLEEP. Allow generous slack for runner jitter.
    assert elapsed < 2 * SLEEP, (
        f"lanes ran sequentially: elapsed={elapsed:.2f}s "
        f"(expected near {SLEEP:.2f}s, sequential would be {3 * SLEEP:.2f}s)"
    )


@pytest.mark.asyncio
async def test_thesis_pipeline_phase_a_runs_parallel(monkeypatch, tmp_path):
    """Phase A — planner ∥ memory ∥ corpus — actually overlaps in time.

    Records each stage's start/end timestamps and asserts that all three
    intervals overlap. Regression guard: refactors that re-serialise the
    pre-pipeline (e.g. awaiting planner before kicking off memory) will
    trip this — sequential execution produces non-overlapping intervals.
    """
    import asyncio
    import time as _time

    from core.memory.episodic import EpisodicMemory
    from core.orchestrator.thesis_flow import ThesisOrchestrator
    from core.router.engine import Router
    from core.schemas import ResearchContext
    from providers.unified import UnifiedLLM
    from tools.mcp.engine import ToolRegistry

    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.chdir(tmp_path)  # isolate per-test qdrant_data lockfile

    SLEEP = 0.30

    unified = UnifiedLLM()
    memory = EpisodicMemory(qdrant_location=":memory:")
    router = Router()
    tools = ToolRegistry()
    orch = ThesisOrchestrator(unified=unified, memory=memory, router=router, tool_registry=tools)

    intervals: list[tuple[str, float, float]] = []

    real_head = orch.head.execute

    async def _timed_planner(task, ctx, mode="planner"):
        if mode != "planner":
            return await real_head(task, ctx, mode=mode)
        start = _time.perf_counter()
        await asyncio.sleep(SLEEP)
        out = await real_head(task, ctx, mode=mode)
        intervals.append(("planner", start, _time.perf_counter()))
        return out

    orch.head.execute = _timed_planner  # type: ignore[method-assign]

    real_memory = orch.memory.retrieve_guidance

    def _timed_memory(*args, **kwargs):
        start = _time.perf_counter()
        _time.sleep(SLEEP)
        out = real_memory(*args, **kwargs)
        intervals.append(("memory", start, _time.perf_counter()))
        return out

    orch.memory.retrieve_guidance = _timed_memory  # type: ignore[method-assign]

    real_corpus = orch.corpus.analyze

    def _timed_corpus(*args, **kwargs):
        start = _time.perf_counter()
        _time.sleep(SLEEP)
        out = real_corpus(*args, **kwargs)
        intervals.append(("corpus", start, _time.perf_counter()))
        return out

    orch.corpus.analyze = _timed_corpus  # type: ignore[method-assign]

    rc = ResearchContext(
        research_question="Does sleep improve recall?",
        topic_summary="A meta-analysis.",
        discipline="psychology",
    )
    await orch.execute(rc)

    spans = {name: (s, e) for name, s, e in intervals}
    assert {
        "planner",
        "memory",
        "corpus",
    } <= spans.keys(), f"missing stage timing: got {sorted(spans.keys())}"
    # Pairwise overlap: every pair of phase-A stages must overlap by at
    # least half a SLEEP (a sequential schedule would have zero overlap).
    pairs = [("planner", "memory"), ("planner", "corpus"), ("memory", "corpus")]
    for a, b in pairs:
        sa, ea = spans[a]
        sb, eb = spans[b]
        overlap = max(0.0, min(ea, eb) - max(sa, sb))
        assert overlap > SLEEP * 0.5, (
            f"{a} and {b} did not overlap: {a}=[{sa:.3f},{ea:.3f}] "
            f"{b}=[{sb:.3f},{eb:.3f}] overlap={overlap:.3f}s"
        )


@pytest.mark.asyncio
async def test_thesis_pipeline_phase_c_runs_parallel(monkeypatch, tmp_path):
    """Phase C — checker ∥ synthesizer — actually overlaps in time."""
    import asyncio
    import time as _time

    from core.memory.episodic import EpisodicMemory
    from core.orchestrator.thesis_flow import ThesisOrchestrator
    from core.router.engine import Router
    from core.schemas import ResearchContext
    from providers.unified import UnifiedLLM
    from tools.mcp.engine import ToolRegistry

    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.chdir(tmp_path)

    SLEEP = 0.40

    unified = UnifiedLLM()
    memory = EpisodicMemory(qdrant_location=":memory:")
    router = Router()
    tools = ToolRegistry()
    orch = ThesisOrchestrator(unified=unified, memory=memory, router=router, tool_registry=tools)

    intervals: list[tuple[str, float, float]] = []

    real_checker = orch.checker.execute

    async def _timed_checker(task, ctx):
        start = _time.perf_counter()
        await asyncio.sleep(SLEEP)
        out = await real_checker(task, ctx)
        intervals.append(("checker", start, _time.perf_counter()))
        return out

    orch.checker.execute = _timed_checker  # type: ignore[method-assign]

    real_synth = orch.synthesizer.execute

    async def _timed_synth(task, ctx):
        start = _time.perf_counter()
        await asyncio.sleep(SLEEP)
        out = await real_synth(task, ctx)
        intervals.append(("synthesizer", start, _time.perf_counter()))
        return out

    orch.synthesizer.execute = _timed_synth  # type: ignore[method-assign]

    rc = ResearchContext(
        research_question="Q",
        topic_summary="S",
        discipline="psychology",
    )
    await orch.execute(rc)

    spans = {name: (s, e) for name, s, e in intervals}
    assert "checker" in spans and "synthesizer" in spans
    cs, ce = spans["checker"]
    ss, se = spans["synthesizer"]
    # Overlap = max(0, min(end_a, end_b) - max(start_a, start_b))
    overlap = max(0.0, min(ce, se) - max(cs, ss))
    assert overlap > SLEEP * 0.5, (
        f"checker and synthesizer did not overlap: "
        f"checker=[{cs:.3f},{ce:.3f}] synth=[{ss:.3f},{se:.3f}] overlap={overlap:.3f}s"
    )


@pytest.mark.asyncio
async def test_thesis_pipeline_auto_saves_session(monkeypatch):
    """Running the thesis pipeline with a session_store auto-saves the session."""
    from core.memory.episodic import EpisodicMemory
    from core.orchestrator.thesis_flow import ThesisOrchestrator
    from core.router.engine import Router
    from core.schemas import ResearchContext
    from core.sessions.store import RedisSessionStore
    from providers.unified import UnifiedLLM
    from tools.mcp.engine import ToolRegistry

    monkeypatch.setenv("DRY_RUN", "true")

    store = RedisSessionStore()
    unified = UnifiedLLM()
    memory = EpisodicMemory(qdrant_location=":memory:")
    router = Router()
    tools = ToolRegistry()

    orch = ThesisOrchestrator(
        unified=unified,
        memory=memory,
        router=router,
        tool_registry=tools,
        session_store=store,
    )

    rc = ResearchContext(
        research_question="Does exercise improve focus?",
        topic_summary="A review of exercise and cognitive performance.",
        discipline="psychology",
    )
    session = await orch.execute(rc)

    assert session.session_id
    loaded = await store.load(session.session_id)
    assert loaded is not None
    assert loaded.session_id == session.session_id
    assert loaded.research_context.research_question == "Does exercise improve focus?"


@pytest.mark.asyncio
async def test_concurrent_executions_do_not_corrupt_self_blackboard_cr02(monkeypatch):
    """CR-02 regression: concurrent execute() on a shared orchestrator must not mutate self.blackboard.

    Pre-fix _execute_inner did `self.blackboard = Blackboard(session_id=session_id)`,
    so the second concurrent call would overwrite the first's blackboard reference
    while the first was still using it — cross-session leakage.
    """
    import asyncio

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
    orch = ThesisOrchestrator(unified=unified, memory=memory, router=router, tool_registry=tools)

    initial_blackboard = orch.blackboard
    initial_session_id = initial_blackboard.session_id

    rc1 = ResearchContext(research_question="Q1", topic_summary="t1", discipline="psychology")
    rc2 = ResearchContext(research_question="Q2", topic_summary="t2", discipline="economics")

    s1, s2 = await asyncio.gather(orch.execute(rc1), orch.execute(rc2))

    # Both executions complete and produced distinct sessions.
    assert s1.session_id != s2.session_id

    # Critically: orch.blackboard reference and session_id are unchanged.
    # Pre-fix this would equal s1.session_id or s2.session_id depending on race timing.
    assert orch.blackboard is initial_blackboard
    assert orch.blackboard.session_id == initial_session_id
