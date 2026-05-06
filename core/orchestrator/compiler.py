import json
import os
from typing import Any, Dict, List

from core.schemas import BlackboardEntry

PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "prompts")

TEMPLATE_MAP = {
    "head_planner": "head_planner.md",
    "head_supervisor": "head_supervisor.md",
    "specialist_researcher": "specialist_researcher.md",
    "specialist_checker": "specialist_checker.md",
    "specialist_synthesizer": "specialist_synthesizer.md",
    "specialist_critic": "specialist_critic.md",
}


class PromptCompiler:
    def __init__(self):
        self._template_cache: Dict[str, str] = {}

    def _load_template(self, name: str) -> str:
        if name not in self._template_cache:
            filename = TEMPLATE_MAP.get(name)
            if not filename:
                raise ValueError(f"Unknown template: {name}")
            path = os.path.join(PROMPT_DIR, filename)
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    self._template_cache[name] = f.read()
            else:
                self._template_cache[name] = f"[Template {name} not found at {path}]"
        return self._template_cache[name]

    def _extract_context(self, entries: List[BlackboardEntry]) -> Dict[str, str]:
        task_entries: List[Dict[str, Any]] = []
        research_contexts: List[Dict[str, Any]] = []
        agent_outputs: List[Dict[str, Any]] = []
        memory_guidance: List[str] = []
        evidence_refs: List[Dict[str, Any]] = []
        route_decisions: List[Dict[str, Any]] = []
        lit_map_entries: List[Dict[str, Any]] = []
        critique_entries: List[Dict[str, Any]] = []
        citation_audit_entries: List[Dict[str, Any]] = []
        synthesis_entries: List[Dict[str, Any]] = []
        lit_search_entries: List[Dict[str, Any]] = []
        corpus_benchmark_entries: List[str] = []

        for entry in entries:
            content = entry.content or {}
            entry_type = entry.entry_type

            if entry_type == "task":
                task_entries.append(content)
                rc = content.get("research_context")
                if rc and isinstance(rc, dict):
                    research_contexts.append(rc)
            elif entry_type == "agent_output":
                agent_outputs.append({
                    "agent_id": content.get("agent_id", ""),
                    "tier": content.get("tier", ""),
                    "result": content.get("result", {}),
                    "confidence": content.get("confidence", 0),
                })
            elif entry_type == "memory_guidance" or entry_type == "route_decision":
                guidance = content.get("guidance") or content.get("rationale")
                if guidance:
                    memory_guidance.append(guidance)
                if entry_type == "route_decision":
                    route_decisions.append(content)
            elif entry_type == "evidence_ref":
                evidence_refs.append({
                    "source": content.get("source", ""),
                    "summary": content.get("summary", ""),
                    "confidence": content.get("confidence", 0),
                })
            elif entry_type == "lit_map":
                lit_map_entries.append(content)
            elif entry_type == "critique":
                critique_entries.append(content)
            elif entry_type == "citation_audit":
                citation_audit_entries.append(content)
            elif entry_type == "lit_search":
                lit_search_entries.append(content)
            elif entry_type == "status" and content.get("entry_type") == "synthesis_report":
                synthesis_entries.append(content)
            elif entry_type == "corpus_benchmarks":
                bm = content.get("benchmarks_text", "")
                if bm:
                    corpus_benchmark_entries.append(bm)

        def _fmt_json(obj: Any) -> str:
            if not obj:
                return "None"
            return json.dumps(obj, indent=2, ensure_ascii=False)

        def _fmt_section(header: str, items: List[Any]) -> str:
            if not items:
                return ""
            return f"## {header}\n" + "\n".join(_fmt_json(i) for i in items)

        def _fmt_list_section(header: str, items: List[str]) -> str:
            if not items:
                return ""
            return f"## {header}\n" + "\n".join(f"- {i}" for i in items)

        context: Dict[str, str] = {}

        context["task_context"] = _fmt_section("TASKS", task_entries) or "No tasks found."
        context["research_context"] = _fmt_section("RESEARCH CONTEXT", research_contexts) or "No research context found."
        context["agent_outputs"] = _fmt_section("AGENT OUTPUTS", agent_outputs) or "No agent outputs yet."
        context["memory_guidance"] = _fmt_list_section("MEMORY GUIDANCE", memory_guidance) or "No memory guidance."
        context["lit_map"] = _fmt_section("LITERATURE MAP", lit_map_entries) or "No literature map yet."
        context["synthesis_report"] = _fmt_section("SYNTHESIS REPORT", synthesis_entries) or "No synthesis report yet."
        context["citation_audit"] = _fmt_section("CITATION AUDIT", citation_audit_entries) or "No citation audit yet."
        context["prior_critiques"] = _fmt_section("PRIOR CRITIQUES", critique_entries) or "No prior critiques."
        context["search_queries"] = json.dumps(
            [{"query": e.get("query", ""), "source": e.get("source", "")} for e in lit_search_entries],
            indent=2,
        ) if lit_search_entries else "No search queries yet."
        context["draft_claims"] = _fmt_section("DRAFT CLAIMS", evidence_refs) or "No claims to check."
        context["corpus_benchmarks"] = "\n".join(corpus_benchmark_entries) if corpus_benchmark_entries else "No corpus data available."

        context["max_searches"] = str(5)
        context["max_papers_per_search"] = str(10)

        return context

    def _render(self, template_name: str, entries: List[BlackboardEntry]) -> str:
        template = self._load_template(template_name)
        context = self._extract_context(entries)
        result = template
        for key, value in context.items():
            placeholder = "{{ " + key + " }}"
            result = result.replace(placeholder, str(value))
        return result

    def compile_head_planner(self, entries: List[BlackboardEntry]) -> str:
        return self._render("head_planner", entries)

    def compile_head_supervisor(self, entries: List[BlackboardEntry]) -> str:
        return self._render("head_supervisor", entries)

    def compile_specialist_researcher(self, entries: List[BlackboardEntry]) -> str:
        return self._render("specialist_researcher", entries)

    def compile_specialist_checker(self, entries: List[BlackboardEntry]) -> str:
        return self._render("specialist_checker", entries)

    def compile_specialist_synthesizer(self, entries: List[BlackboardEntry]) -> str:
        return self._render("specialist_synthesizer", entries)

    def compile_specialist_critic(self, entries: List[BlackboardEntry]) -> str:
        return self._render("specialist_critic", entries)

    def compile_head_system_prompt(self, entries: List[BlackboardEntry]) -> str:
        """Backward-compatible: routes to head_supervisor by default."""
        return self.compile_head_supervisor(entries)
