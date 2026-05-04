import json
from typing import Any, Dict, List, Optional

from core.schemas import (
    ResearchSession,
    LitMap,
    LiteratureResult,
    CitationAudit,
    CritiqueResult,
    SynthesisReport,
)


def _papers_text(papers: List[Any], max_show: int = 20) -> str:
    if not papers:
        return "  (none)"
    lines = []
    for i, p in enumerate(papers[:max_show]):
        if isinstance(p, dict):
            pid = p.get("paper_id") or p.get("arxiv_id") or p.get("doi", "?")
            title = p.get("title", "?")
            year = p.get("year", "?")
            lines.append(f"  {i+1}. [{year}] {title[:80]}")
            lines.append(f"     ID: {pid}")
        elif isinstance(p, LiteratureResult):
            lines.append(f"  {i+1}. [{p.year}] {p.title[:80]}")
            lines.append(f"     ID: {p.paper_id}")
        else:
            lines.append(f"  {i+1}. {str(p)[:100]}")
    if len(papers) > max_show:
        lines.append(f"  ... and {len(papers) - max_show} more")
    return "\n".join(lines)


def format_lit_map_text(lit_map: LitMap) -> str:
    lines = ["=== LITERATURE MAP ===", f"Research Question: {lit_map.research_question}"]
    lines.append(f"Total Found: {lit_map.total_found} | Query: {lit_map.search_query_used}")
    lines.append("")
    lines.append("--- Supporting ---")
    lines.append(_papers_text(lit_map.supporting))
    lines.append("")
    lines.append("--- Challenging ---")
    lines.append(_papers_text(lit_map.challenging))
    lines.append("")
    lines.append("--- Adjacent ---")
    lines.append(_papers_text(lit_map.adjacent))
    return "\n".join(lines)


def format_citation_audit_text(audit: CitationAudit) -> str:
    lines = ["=== CITATION AUDIT ==="]
    lines.append(f"Claims Checked: {audit.claims_checked} | Verified: {audit.verified_claims}")
    if audit.missing_citations:
        lines.append(f"\nMissing Citations ({len(audit.missing_citations)}):")
        for c in audit.missing_citations[:10]:
            lines.append(f"  - {c}")
    if audit.weak_citations:
        lines.append(f"\nWeak Citations ({len(audit.weak_citations)}):")
        for c in audit.weak_citations[:10]:
            lines.append(f"  - {c}")
    if audit.contested_claims:
        lines.append(f"\nContested Claims ({len(audit.contested_claims)}):")
        for c in audit.contested_claims[:10]:
            lines.append(f"  - {c}")
    if audit.bibtex_entries:
        lines.append(f"\nBibTeX Entries: {len(audit.bibtex_entries)}")
    return "\n".join(lines)


def format_critique_text(critique: CritiqueResult) -> str:
    lines = ["=== CRITIQUE ==="]

    def _section(heading: str, items: List[str]):
        out = [f"\n## {heading}"]
        if items:
            for item in items:
                out.append(f"  - {item}")
        else:
            out.append("  (none)")
        return out

    if critique.strengths:
        lines.extend(_section("Strengths", critique.strengths))
    if critique.weaknesses:
        lines.extend(_section("Weaknesses", critique.weaknesses))
    if critique.gaps:
        lines.extend(_section("Gaps", critique.gaps))
    if critique.counterarguments:
        lines.extend(_section("Counterarguments", critique.counterarguments))
    if critique.suggestions:
        lines.extend(_section("Suggestions", critique.suggestions))
    if critique.methodological_notes:
        lines.extend(_section("Methodological Notes", critique.methodological_notes))
    lines.append("")
    if critique.overall_assessment:
        lines.append(f"Overall: {critique.overall_assessment}")
    else:
        lines.append("Overall: (no assessment)")
    return "\n".join(lines)


def format_synthesis_text(sr: SynthesisReport) -> str:
    lines = ["=== SYNTHESIS REPORT ==="]
    if sr.research_question:
        lines.append(f"Research Question: {sr.research_question}")
    lines.append("")

    def _dict_section(heading: str, d: Dict[str, Any]):
        if not d:
            return []
        out = [f"## {heading}"]
        for k, v in d.items():
            if isinstance(v, list):
                out.append(f"  {k}:")
                for item in v[:5]:
                    out.append(f"    - {str(item)[:100]}")
            else:
                out.append(f"  {k}: {str(v)[:100]}")
        return out

    if sr.method_summary:
        lines.extend(_dict_section("Methods", sr.method_summary))
    if sr.dataset_summary:
        lines.extend(_dict_section("Datasets", sr.dataset_summary))
    if sr.metric_summary:
        lines.extend(_dict_section("Metrics", sr.metric_summary))
    if sr.corpus_insights:
        lines.extend(_dict_section("Corpus Insights", sr.corpus_insights))
    if sr.recommended_reading:
        lines.append(f"\n## Recommended Reading ({len(sr.recommended_reading)})")
        for rec in sr.recommended_reading[:5]:
            lines.append(f"  - {rec.get('paper_id', '?')}: {rec.get('reason', '')[:100]}")
    if sr.cross_paper_comparisons:
        lines.append(f"\n## Cross-Paper Comparisons ({len(sr.cross_paper_comparisons)})")
        for comp in sr.cross_paper_comparisons[:5]:
            dim = comp.get("dimension", "?")
            comp_text = comp.get("comparison", str(comp))[:120]
            lines.append(f"  - [{dim}] {comp_text}")
    return "\n".join(lines)


def format_session_text(session: ResearchSession) -> str:
    lines = ["=" * 60]
    lines.append(f"RESEARCH SESSION: {session.session_id}")
    lines.append(f"Status: {session.status} | Created: {session.created_at}")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Research Question: {session.research_context.research_question}")
    lines.append(f"Discipline: {session.research_context.discipline}")
    lines.append(f"Topic: {session.research_context.topic_summary}")
    lines.append("")

    if session.research_plan:
        lines.append("--- Research Plan ---")
        rp = session.research_plan
        lines.append(f"  Plan ID: {rp.plan_id}")
        lines.append(f"  Strategy: {rp.strategy}")
        if rp.subquestions:
            lines.append(f"  Subquestions:")
            for sq in rp.subquestions:
                lines.append(f"    - {sq}")
        if rp.search_lanes:
            lines.append(f"  Search Lanes: {len(rp.search_lanes)}")
        lines.append("")

    if session.lit_map:
        lines.append(format_lit_map_text(session.lit_map))
        lines.append("")

    if session.citation_audit:
        lines.append(format_citation_audit_text(session.citation_audit))
        lines.append("")

    if session.synthesis_report:
        lines.append(format_synthesis_text(session.synthesis_report))
        lines.append("")

    if session.critique:
        lines.append(format_critique_text(session.critique))
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def format_as_json(obj: Any) -> str:
    if hasattr(obj, "model_dump"):
        d = obj.model_dump()
    elif hasattr(obj, "__dict__"):
        d = {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
    else:
        d = obj
    return json.dumps(d, indent=2, default=str, ensure_ascii=False)
