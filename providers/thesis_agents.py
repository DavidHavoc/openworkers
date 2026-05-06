import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Type

from pydantic import BaseModel

from core.orchestrator.compiler import PromptCompiler
from core.schemas import (
    BlackboardEntry,
    CitationAudit,
    CritiqueResult,
    LiteratureResult,
    LitMap,
    ResearchPlan,
    SynthesisReport,
    Task,
)
from providers.unified import UnifiedLLM

logger = logging.getLogger(__name__)


_MODEL_SCHEMAS: Dict[Type[BaseModel], Dict[str, Any]] = {}
_SCHEMA_NAMES: Dict[Type[BaseModel], str] = {
    ResearchPlan: "ResearchPlan",
    LitMap: "LitMap",
    CitationAudit: "CitationAudit",
    SynthesisReport: "SynthesisReport",
    CritiqueResult: "CritiqueResult",
}


def _schema_for(model_cls: Type[BaseModel]) -> Dict[str, Any]:
    if model_cls not in _MODEL_SCHEMAS:
        raw = model_cls.model_json_schema()
        raw.pop("title", None)
        _MODEL_SCHEMAS[model_cls] = raw
    return _MODEL_SCHEMAS[model_cls]


def _parse_json_response(text: str) -> Any:
    """Handle LLM JSON variance: code blocks, trailing commas, single quotes, missing fields."""
    if not text or not text.strip():
        return {}

    cleaned = text.strip()

    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    if cleaned.startswith("{") and not cleaned.endswith("}"):
        cleaned += "}"
    if cleaned.startswith("[") and not cleaned.endswith("]"):
        cleaned += "]"

    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    cleaned = re.sub(r"(\w+)'s\b", r"\1's", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    try:
        cleaned_single = cleaned.replace("'", '"')
        return json.loads(cleaned_single)
    except json.JSONDecodeError:
        pass

    try:
        result: Dict[str, Any] = {}
        for key in re.findall(r'"(\w+)":\s*\[', cleaned):
            array_match = re.search(rf'"{key}":\s*\[(.*?)\]', cleaned, re.DOTALL)
            if array_match:
                items = re.findall(r'"([^"]*)"', array_match.group(1))
                result[key] = items
        if result:
            return result
    except Exception:
        pass

    logger.warning(f"Could not parse JSON from LLM response: {text[:200]}...")
    return {"_parse_error": True, "raw": text[:500]}


def _parse_structured_output(
    text: str,
    model_cls: Type[BaseModel],
    dict_converter: Callable[[Dict[str, Any]], Any],
) -> Any:
    """Pydantic model_validate_json first; legacy dict parser as fallback."""
    if not text or not text.strip():
        return dict_converter({})

    cleaned = text.strip()

    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()

    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)

    for attempt in (cleaned, cleaned.replace("'", '"')):
        try:
            return model_cls.model_validate_json(attempt)
        except Exception:
            continue

    parsed_dict = _parse_json_response(cleaned)
    return dict_converter(parsed_dict)


def _build_placeholder_research_plan(question: str) -> ResearchPlan:
    return ResearchPlan(
        plan_id="placeholder-plan-001",
        research_question=question,
        subquestions=["What does the existing literature say?", "What methodological gaps exist?"],
        strategy="broad_survey",
        search_lanes=[
            {
                "query": "placeholder search",
                "source": "semantic_scholar",
                "purpose": "initial survey",
            },
        ],
        evidence_needs=["literature review", "methodology assessment"],
        budget_allocation={"max_searches": 5, "max_papers_per_search": 10},
        suggested_methodology="systematic review",
    )


def _build_placeholder_lit_map(question: str) -> LitMap:
    return LitMap(
        research_question=question,
        supporting=[],
        challenging=[],
        adjacent=[],
        total_found=0,
        search_query_used="placeholder_query",
    )


def _build_placeholder_citation_audit() -> CitationAudit:
    return CitationAudit(claims_checked=0, verified_claims=0)


def _build_placeholder_synthesis_report(question: str = "") -> SynthesisReport:
    return SynthesisReport(
        research_question=question,
        method_summary={},
        dataset_summary={},
        metric_summary={},
        corpus_insights={},
        recommended_reading=[],
        cross_paper_comparisons=[],
    )


def _build_placeholder_critique_result() -> CritiqueResult:
    return CritiqueResult(
        strengths=[],
        weaknesses=[],
        gaps=[],
        counterarguments=[],
        suggestions=[],
        methodological_notes=[],
        overall_assessment="",
    )


class ThesisHeadProvider:
    def __init__(self, unified: UnifiedLLM, compiler: Optional[PromptCompiler] = None):
        self.unified = unified
        self.compiler = compiler or PromptCompiler()

    async def execute(
        self,
        task: Task,
        context: Optional[Dict[str, Any]] = None,
        mode: str = "planner",
    ) -> Dict[str, Any]:
        context = context or {}
        entries: List[BlackboardEntry] = context.get("blackboard_entries", [])

        if mode == "planner":
            return await self._execute_planner(task, entries)
        elif mode == "supervisor":
            return await self._execute_supervisor(task, entries)
        else:
            raise ValueError(f"Unknown head mode: {mode}")

    async def _execute_planner(self, task: Task, entries: List[BlackboardEntry]) -> Dict[str, Any]:
        prompt = f"Research Question: {task.description}"
        system_prompt = self.compiler.compile_head_planner(entries)
        response = await self.unified.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            mode="quality",
            response_schema=_schema_for(ResearchPlan),
        )

        if response.dry_run:
            question = (
                task.research_context.research_question
                if task.research_context
                else task.description
            )
            plan = _build_placeholder_research_plan(question)
        else:
            plan = _parse_structured_output(response.content, ResearchPlan, _dict_to_research_plan)

        return {
            "tier": "head",
            "mode": "planner",
            "status": "success",
            "output": plan,
            "provider": response.provider_used,
            "model": response.model,
            "latency_ms": response.latency_ms,
            "cost_estimate_usd": response.cost_estimate_usd,
            "dry_run": response.dry_run,
            "fallback_used": response.fallback_used,
        }

    async def _execute_supervisor(
        self, task: Task, entries: List[BlackboardEntry]
    ) -> Dict[str, Any]:
        prompt = (
            f"Review all agent findings and produce a structured critique for: {task.description}"
        )
        system_prompt = self.compiler.compile_head_supervisor(entries)
        response = await self.unified.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            mode="quality",
            response_schema=_schema_for(CritiqueResult),
        )

        if response.dry_run:
            critique = _build_placeholder_critique_result()
        else:
            critique = _parse_structured_output(
                response.content, CritiqueResult, _dict_to_critique_result
            )

        return {
            "tier": "head",
            "mode": "supervisor",
            "status": "success",
            "output": critique,
            "provider": response.provider_used,
            "model": response.model,
            "latency_ms": response.latency_ms,
            "cost_estimate_usd": response.cost_estimate_usd,
            "dry_run": response.dry_run,
            "fallback_used": response.fallback_used,
        }


class ResearcherAgent:
    def __init__(self, unified: UnifiedLLM, compiler: Optional[PromptCompiler] = None):
        self.unified = unified
        self.compiler = compiler or PromptCompiler()

    async def execute(self, task: Task, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = context or {}
        entries: List[BlackboardEntry] = context.get("blackboard_entries", [])

        question = (
            task.research_context.research_question if task.research_context else task.description
        )
        prompt = f"Search for papers relevant to: {question}\n\nTask: {task.description}"
        system_prompt = self.compiler.compile_specialist_researcher(entries)
        response = await self.unified.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            mode="cheap",
            response_schema=_schema_for(LitMap),
        )

        if response.dry_run:
            lit_map = _build_placeholder_lit_map(question)
        else:
            lit_map = _parse_structured_output(response.content, LitMap, _dict_to_lit_map)

        return {
            "agent": "researcher",
            "tier": "worker",
            "status": "success",
            "output": lit_map,
            "provider": response.provider_used,
            "model": response.model,
            "latency_ms": response.latency_ms,
            "cost_estimate_usd": response.cost_estimate_usd,
            "dry_run": response.dry_run,
            "fallback_used": response.fallback_used,
        }


class CheckerAgent:
    def __init__(self, unified: UnifiedLLM, compiler: Optional[PromptCompiler] = None):
        self.unified = unified
        self.compiler = compiler or PromptCompiler()

    async def execute(self, task: Task, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = context or {}
        entries: List[BlackboardEntry] = context.get("blackboard_entries", [])

        prompt = f"Verify all citations and claims for: {task.description}"
        system_prompt = self.compiler.compile_specialist_checker(entries)
        response = await self.unified.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            mode="balanced",
            response_schema=_schema_for(CitationAudit),
        )

        if response.dry_run:
            audit = _build_placeholder_citation_audit()
        else:
            audit = _parse_structured_output(
                response.content, CitationAudit, _dict_to_citation_audit
            )

        return {
            "agent": "checker",
            "tier": "middle",
            "status": "success",
            "output": audit,
            "provider": response.provider_used,
            "model": response.model,
            "latency_ms": response.latency_ms,
            "cost_estimate_usd": response.cost_estimate_usd,
            "dry_run": response.dry_run,
            "fallback_used": response.fallback_used,
        }


class SynthesizerAgent:
    def __init__(self, unified: UnifiedLLM, compiler: Optional[PromptCompiler] = None):
        self.unified = unified
        self.compiler = compiler or PromptCompiler()

    async def execute(self, task: Task, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = context or {}
        entries: List[BlackboardEntry] = context.get("blackboard_entries", [])

        prompt = (
            f"Extract methods, datasets, and metrics from the literature for: {task.description}"
        )
        system_prompt = self.compiler.compile_specialist_synthesizer(entries)
        response = await self.unified.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            mode="balanced",
            response_schema=_schema_for(SynthesisReport),
        )

        if response.dry_run:
            question = task.research_context.research_question if task.research_context else ""
            report = _build_placeholder_synthesis_report(question)
        else:
            report = _parse_structured_output(
                response.content, SynthesisReport, _dict_to_synthesis_report
            )

        return {
            "agent": "synthesizer",
            "tier": "middle",
            "status": "success",
            "output": report,
            "provider": response.provider_used,
            "model": response.model,
            "latency_ms": response.latency_ms,
            "cost_estimate_usd": response.cost_estimate_usd,
            "dry_run": response.dry_run,
            "fallback_used": response.fallback_used,
        }


class CriticAgent:
    def __init__(self, unified: UnifiedLLM, compiler: Optional[PromptCompiler] = None):
        self.unified = unified
        self.compiler = compiler or PromptCompiler()

    async def execute(self, task: Task, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        context = context or {}
        entries: List[BlackboardEntry] = context.get("blackboard_entries", [])

        prompt = f"Critique the research and find counterarguments for: {task.description}"
        system_prompt = self.compiler.compile_specialist_critic(entries)
        response = await self.unified.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            mode="quality",
            response_schema=_schema_for(CritiqueResult),
        )

        if response.dry_run:
            critique = _build_placeholder_critique_result()
        else:
            critique = _parse_structured_output(
                response.content, CritiqueResult, _dict_to_critique_result
            )

        return {
            "agent": "critic",
            "tier": "head",
            "status": "success",
            "output": critique,
            "provider": response.provider_used,
            "model": response.model,
            "latency_ms": response.latency_ms,
            "cost_estimate_usd": response.cost_estimate_usd,
            "dry_run": response.dry_run,
            "fallback_used": response.fallback_used,
        }


def _dict_to_research_plan(d: Dict[str, Any]) -> ResearchPlan:
    return ResearchPlan(
        plan_id=str(d.get("plan_id", "")),
        research_question=str(d.get("research_question", "")),
        subquestions=_list_str(d.get("subquestions")),
        strategy=str(d.get("strategy", "")),
        search_lanes=_list_dict(d.get("search_lanes") or d.get("search_queries")),
        evidence_needs=_list_str(d.get("evidence_needs", [])),
        budget_allocation=d.get("budget", {}) or {},
        suggested_methodology=str(d.get("suggested_methodology", "")),
    )


def _dict_to_lit_map(d: Dict[str, Any]) -> LitMap:
    return LitMap(
        research_question=str(d.get("research_question", "")),
        supporting=_list_literature_results(d.get("supporting")),
        challenging=_list_literature_results(d.get("challenging")),
        adjacent=_list_literature_results(d.get("adjacent")),
        total_found=int(d.get("total_found", 0)),
        search_query_used=str(d.get("search_query_used", "")),
    )


def _dict_to_citation_audit(d: Dict[str, Any]) -> CitationAudit:
    return CitationAudit(
        claims_checked=int(d.get("claims_checked", 0)),
        verified_claims=int(d.get("verified_claims", 0)),
        missing_citations=_list_str(d.get("missing_citations")),
        weak_citations=_list_str(d.get("weak_citations")),
        contested_claims=_list_str(d.get("contested_claims")),
        bibtex_entries=d.get("bibtex_entries", {}) or {},
    )


def _dict_to_synthesis_report(d: Dict[str, Any]) -> SynthesisReport:
    return SynthesisReport(
        research_question=str(d.get("research_question", "")),
        method_summary=_dict_list_str(d.get("method_summary", {})),
        dataset_summary=d.get("dataset_summary", {}) or {},
        metric_summary=d.get("metric_summary", {}) or {},
        corpus_insights=_dict_list_str(d.get("corpus_insights", {})),
        recommended_reading=_list_dict(d.get("recommended_reading")),
        cross_paper_comparisons=_list_dict(d.get("cross_paper_comparisons")),
    )


def _dict_to_critique_result(d: Dict[str, Any]) -> CritiqueResult:
    return CritiqueResult(
        strengths=_list_str(d.get("strengths")),
        weaknesses=_list_str(d.get("weaknesses")),
        gaps=_list_str(d.get("gaps")),
        counterarguments=_list_str(d.get("counterarguments")),
        suggestions=_list_str(d.get("suggestions")),
        methodological_notes=_list_str(d.get("methodological_notes")),
        overall_assessment=str(d.get("overall_assessment", "")),
    )


def _list_str(val: Any) -> List[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(v) for v in val]
    return [str(val)]


def _list_dict(val: Any) -> List[Dict[str, Any]]:
    if val is None:
        return []
    if isinstance(val, list):
        return [v if isinstance(v, dict) else {"value": str(v)} for v in val]
    return [{"value": str(val)}]


def _dict_list_str(val: Any) -> Dict[str, List[str]]:
    if val is None:
        return {}
    if isinstance(val, dict):
        return {str(k): _list_str(v) for k, v in val.items()}
    return {}


def _list_literature_results(val: Any) -> List[LiteratureResult]:
    if val is None:
        return []
    if not isinstance(val, list):
        return []
    results: List[LiteratureResult] = []
    for item in val:
        if not isinstance(item, dict):
            continue
        results.append(
            LiteratureResult(
                paper_id=str(item.get("paper_id", "")),
                title=str(item.get("title", "")),
                authors=_list_str(item.get("authors")),
                year=int(item.get("year", 0)),
                abstract=str(item.get("abstract", "")),
                url=str(item.get("url", "")),
                source=str(item.get("source", "")),
                citation_count=int(item.get("citation_count", 0)),
                extracted_claims=_list_str(item.get("extracted_claims")),
            )
        )
    return results
