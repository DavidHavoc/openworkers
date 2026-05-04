import asyncio
import json
import uuid
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

from core.schemas import (
    Task,
    BlackboardEntry,
    ResearchContext,
    ResearchPlan,
    ResearchSession,
    LitMap,
    LiteratureResult,
    CitationAudit,
    SynthesisReport,
    CritiqueResult,
    MemoryBrief,
    BudgetState,
    MemoryEpisode,
    EpisodeRoute,
    EpisodeModels,
    EpisodeMetrics,
    EpisodeQuality,
)
from core.blackboard.engine import Blackboard
from core.memory.episodic import EpisodicMemory
from core.router.engine import Router
from providers.unified import UnifiedLLM
from providers.thesis_agents import (
    ThesisHeadProvider,
    ResearcherAgent,
    CheckerAgent,
    SynthesizerAgent,
    CriticAgent,
    _build_placeholder_lit_map,
    _build_placeholder_citation_audit,
    _build_placeholder_synthesis_report,
    _build_placeholder_critique_result,
    _build_placeholder_research_plan,
)
from core.orchestrator.compiler import PromptCompiler
from core.observability.metrics import obs_logger


class ThesisOrchestrator:
    def __init__(
        self,
        unified: UnifiedLLM,
        memory: EpisodicMemory,
        router: Router,
        blackboard: Blackboard = None,
        tool_registry=None,
    ):
        self.unified = unified
        self.memory = memory
        self.router = router
        self.blackboard = blackboard or Blackboard()
        self.tool_registry = tool_registry
        self.compiler = PromptCompiler()

        self.head = ThesisHeadProvider(unified=unified, compiler=self.compiler)
        self.researcher = ResearcherAgent(unified=unified, compiler=self.compiler)
        self.checker = CheckerAgent(unified=unified, compiler=self.compiler)
        self.synthesizer = SynthesizerAgent(unified=unified, compiler=self.compiler)
        self.critic = CriticAgent(unified=unified, compiler=self.compiler)

    def _add_entry(self, entry_type: str, content: Dict[str, Any]) -> Optional[BlackboardEntry]:
        try:
            return self.blackboard.add_entry(entry_type, content)
        except Exception:
            pass
        return None

    def _get_entries(self) -> List[BlackboardEntry]:
        try:
            return self._get_entries()
        except Exception:
            return []

    async def execute(self, research_context: ResearchContext) -> ResearchSession:
        start_time = time.time()
        session_id = str(uuid.uuid4())
        errors: List[str] = []

        try:
            self.blackboard = Blackboard(session_id=session_id)
        except Exception:
            self.blackboard = Blackboard(session_id=session_id)

        task = Task(
            task_id=str(uuid.uuid4()),
            task_type="thesis",
            description=research_context.research_question,
            complexity_estimated="medium",
            research_context=research_context,
        )
        self._add_entry("task", task.model_dump())

        obs_logger.log_event("thesis_pipeline_started", session_id, {
            "research_question": research_context.research_question,
            "discipline": research_context.discipline,
        })

        # ── Stage 1: HEAD planner ──
        research_plan: Optional[ResearchPlan] = None
        try:
            result = await self.head.execute(
                task,
                {"blackboard_entries": self._get_entries()},
                mode="planner",
            )
            research_plan = result["output"]
            self._add_entry("agent_output", result)
            obs_logger.log_event("stage_completed", session_id, {"stage": "head_planner", "status": "success"})
        except Exception as e:
            errors.append(f"head_planner: {e}")
            research_plan = _build_placeholder_research_plan(research_context.research_question)
            obs_logger.log_event("stage_failed", session_id, {"stage": "head_planner", "error": str(e)})

        # ── Stage 2: Memory retrieval ──
        memory_brief: Optional[MemoryBrief] = None
        try:
            memory_brief = self.memory.retrieve_guidance(
                research_context.research_question, task_type="thesis"
            )
            self._add_entry("memory_guidance", {
                "guidance": memory_brief.to_formatted_string(),
            })
            obs_logger.log_memory_hit(session_id, "thesis", memory_brief.similar_past_tasks_count)
        except Exception as e:
            errors.append(f"memory_retrieval: {e}")
            memory_brief = MemoryBrief()

        # ── Stage 3: Researcher (literature search) ──
        lit_map: Optional[LitMap] = None
        try:
            raw_papers = await self._search_literature_from_plan(research_plan)
            if raw_papers:
                deduped = self._deduplicate_papers(raw_papers)
                self._add_entry("lit_search", {
                    "query": research_plan.search_lanes[0].get("query", "") if research_plan.search_lanes else "",
                    "source": research_plan.search_lanes[0].get("source", "") if research_plan.search_lanes else "",
                    "raw_results": len(raw_papers),
                    "deduplicated": len(deduped),
                    "papers": [p if isinstance(p, dict) else p.model_dump() for p in deduped],
                })
                search_context = self._get_entries()
            else:
                search_context = self._get_entries()

            result = await self.researcher.execute(
                task, {"blackboard_entries": search_context}
            )
            lit_map = result["output"]
            self._add_entry("lit_map", lit_map.model_dump())
            obs_logger.log_event("stage_completed", session_id, {"stage": "researcher", "status": "success"})
        except Exception as e:
            errors.append(f"researcher: {e}")
            lit_map = _build_placeholder_lit_map(research_context.research_question)
            obs_logger.log_event("stage_failed", session_id, {"stage": "researcher", "error": str(e)})

        # ── Stage 4: Checker (citation audit) ──
        citation_audit: Optional[CitationAudit] = None
        try:
            result = await self.checker.execute(
                task, {"blackboard_entries": self._get_entries()}
            )
            citation_audit = result["output"]
            self._add_entry("citation_audit", citation_audit.model_dump())
            obs_logger.log_event("stage_completed", session_id, {"stage": "checker", "status": "success"})
        except Exception as e:
            errors.append(f"checker: {e}")
            citation_audit = _build_placeholder_citation_audit()
            obs_logger.log_event("stage_failed", session_id, {"stage": "checker", "error": str(e)})

        # ── Stage 5: Synthesizer ──
        synthesis_report: Optional[SynthesisReport] = None
        try:
            result = await self.synthesizer.execute(
                task, {"blackboard_entries": self._get_entries()}
            )
            synthesis_report = result["output"]
            self._add_entry("status", {
                "entry_type": "synthesis_report",
                "content": synthesis_report.model_dump(),
            })
            obs_logger.log_event("stage_completed", session_id, {"stage": "synthesizer", "status": "success"})
        except Exception as e:
            errors.append(f"synthesizer: {e}")
            synthesis_report = _build_placeholder_synthesis_report(
                research_context.research_question
            )
            obs_logger.log_event("stage_failed", session_id, {"stage": "synthesizer", "error": str(e)})

        # ── Stage 6: Critic ──
        critique: Optional[CritiqueResult] = None
        try:
            result = await self.critic.execute(
                task, {"blackboard_entries": self._get_entries()}
            )
            critique = result["output"]
            self._add_entry("critique", critique.model_dump())
            obs_logger.log_event("stage_completed", session_id, {"stage": "critic", "status": "success"})
        except Exception as e:
            errors.append(f"critic: {e}")
            critique = _build_placeholder_critique_result()
            obs_logger.log_event("stage_failed", session_id, {"stage": "critic", "error": str(e)})

        # ── Stage 7: HEAD final pass (supervisor) ──
        final_critique: Optional[CritiqueResult] = None
        try:
            result = await self.head.execute(
                task,
                {"blackboard_entries": self._get_entries()},
                mode="supervisor",
            )
            final_critique = result["output"]
            self._add_entry("critique", final_critique.model_dump())
            obs_logger.log_event("stage_completed", session_id, {"stage": "head_supervisor", "status": "success"})
        except Exception as e:
            errors.append(f"head_supervisor: {e}")
            final_critique = _build_placeholder_critique_result()
            obs_logger.log_event("stage_failed", session_id, {"stage": "head_supervisor", "error": str(e)})

        # ── Stage 8: Assemble ResearchSession ──
        elapsed_ms = int((time.time() - start_time) * 1000)
        session = ResearchSession(
            session_id=session_id,
            research_context=research_context,
            research_plan=research_plan,
            lit_map=lit_map,
            citation_audit=citation_audit,
            synthesis_report=synthesis_report,
            critique=final_critique,
            created_at=datetime.utcnow().isoformat() + "Z",
            status="complete" if not errors else "partial",
        )

        # ── Store episode in memory ──
        try:
            episode = MemoryEpisode(
                episode_id=str(uuid.uuid4()),
                timestamp=datetime.utcnow().isoformat() + "Z",
                task_summary=research_context.research_question,
                task_type="thesis",
                privacy_tier="public",
                route=EpisodeRoute(
                    head_direct=False,
                    used_middle_tier=True,
                    used_worker_swarm=True,
                    used_mcp_tools=list(self.tool_registry._tools.keys()) if self.tool_registry else [],
                    spawn_count=5,
                ),
                models=EpisodeModels(
                    head="thesis_head",
                    middle=["checker", "synthesizer"],
                    workers=["researcher"],
                ),
                metrics=EpisodeMetrics(
                    latency_ms=elapsed_ms,
                    input_tokens=0,
                    output_tokens=0,
                    estimated_cost_usd=0.01,
                ),
                quality=EpisodeQuality(
                    score=0.9 if not errors else 0.5,
                    accepted=not bool(errors),
                    confidence=0.8,
                ),
                failures=errors,
                routing_takeaway="thesis_pipeline_completed",
            )
            self.memory.store_episode(episode)
        except Exception as e:
            obs_logger.log_event("store_episode_failed", session_id, {"error": str(e)})

        obs_logger.log_trace(session_id, "thesis_pipeline", elapsed_ms, not bool(errors))
        obs_logger.log_event("thesis_pipeline_completed", session_id, {
            "session_id": session_id,
            "stages": 8,
            "errors": len(errors),
            "elapsed_ms": elapsed_ms,
        })

        return session

    async def _search_literature_from_plan(self, plan: Optional[ResearchPlan]) -> List[Dict[str, Any]]:
        if plan is None or not plan.search_lanes:
            return []
        if self.tool_registry is None:
            return []

        all_papers: List[Dict[str, Any]] = []
        for lane in plan.search_lanes[: min(len(plan.search_lanes), 3)]:
            query = lane.get("query", "")
            source = lane.get("source", "semantic_scholar")
            if not query:
                continue
            papers = await self._search_literature_raw_inner(query, source, limit=10)
            all_papers.extend(papers)
        return all_papers

    def _deduplicate_papers(self, papers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen: set = set()
        deduped: List[Dict[str, Any]] = []
        for p in papers:
            pid = p.get("paper_id") or p.get("arxiv_id") or p.get("doi", "")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            deduped.append(p)
        return deduped

    async def _search_literature_raw_inner(
        self, query: str, source: str = "semantic_scholar", limit: int = 10
    ) -> List[Dict[str, Any]]:
        if self.tool_registry is None:
            return []

        tool_name = "arxiv_search" if source == "arxiv" else "semantic_scholar_search"
        tool = self.tool_registry.get_tool(tool_name)
        if tool is None:
            return []

        try:
            params = {"query": query}
            if tool_name == "arxiv_search":
                params["max_results"] = limit
            else:
                params["limit"] = limit
            result = await tool.execute(params, "public")
            if "error" in result:
                return []
            papers = result.get("papers", [])
            for p in papers:
                p["source"] = source
            return papers
        except Exception:
            return []

    async def _search_literature_raw(
        self, query: str, source: str = "semantic_scholar", limit: int = 10
    ) -> List[Dict[str, Any]]:
        return await self._search_literature_raw_inner(query, source, limit)

    async def _verify_single_citation(
        self, claim: str = "", doi_or_title: str = ""
    ) -> Dict[str, Any]:
        if self.tool_registry is None:
            return {"exists": False, "error": "No tool registry available"}

        tool = self.tool_registry.get_tool("crossref_verification")
        if tool is None:
            return {"exists": False, "error": "Tool not found"}

        try:
            return await tool.execute({"doi": doi_or_title.strip()}, "public")
        except Exception as e:
            return {"exists": False, "error": str(e)}

    async def _critique_only(self, research_context: ResearchContext) -> CritiqueResult:
        task = Task(
            task_id=str(uuid.uuid4()),
            task_type="thesis",
            description=research_context.research_question,
            complexity_estimated="medium",
            research_context=research_context,
        )
        dummy_entries = [
            BlackboardEntry(
                entry_id="e1",
                entry_type="task",
                content=task.model_dump(),
                metadata={},
                timestamp=datetime.utcnow().isoformat() + "Z",
            ),
        ]

        try:
            result = await self.head.execute(
                task,
                {"blackboard_entries": dummy_entries},
                mode="supervisor",
            )
            return result["output"]
        except Exception as e:
            return _build_placeholder_critique_result()
