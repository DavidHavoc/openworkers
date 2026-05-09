"""Shared placeholder builders used by adapters, unified, and thesis_flow.

These helpers fabricate well-formed but empty results for two cases:
* ``generate_placeholder_json`` — DRY_RUN mode in the LLM adapter layer.
* ``build_placeholder_*`` — orchestrator fallback when an agent fails or
  is skipped, so downstream stages always receive a valid object.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from core.schemas import (
    CitationAudit,
    CritiqueResult,
    LitMap,
    ResearchPlan,
    SynthesisReport,
)


def generate_placeholder_json(schema: Dict[str, Any]) -> str:
    props = schema.get("properties", {})
    required = schema.get("required", [])
    result: Dict[str, Any] = {}
    for key, prop in props.items():
        prop_type = prop.get("type", "string")
        if prop_type == "string":
            result[key] = "[DRY_RUN]" if key in required else ""
        elif prop_type in ("integer", "number"):
            result[key] = 0
        elif prop_type == "boolean":
            result[key] = False
        elif prop_type == "array":
            result[key] = []
        elif prop_type == "object":
            result[key] = {}
    return json.dumps(result)


def build_placeholder_research_plan(question: str) -> ResearchPlan:
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


def build_placeholder_lit_map(question: str) -> LitMap:
    return LitMap(
        research_question=question,
        supporting=[],
        challenging=[],
        adjacent=[],
        total_found=0,
        search_query_used="placeholder_query",
    )


def build_placeholder_citation_audit() -> CitationAudit:
    return CitationAudit(claims_checked=0, verified_claims=0)


def build_placeholder_synthesis_report(question: str = "") -> SynthesisReport:
    return SynthesisReport(
        research_question=question,
        method_summary={},
        dataset_summary={},
        metric_summary={},
        corpus_insights={},
        recommended_reading=[],
        cross_paper_comparisons=[],
    )


def build_placeholder_critique_result() -> CritiqueResult:
    return CritiqueResult(
        strengths=[],
        weaknesses=[],
        gaps=[],
        counterarguments=[],
        suggestions=[],
        methodological_notes=[],
        overall_assessment="",
    )
