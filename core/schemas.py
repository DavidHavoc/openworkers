from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field

class UserRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    priority: int = 1

class SessionState(BaseModel):
    session_id: str
    status: str
    current_task_id: Optional[str] = None
    created_at: str

class ResearchContext(BaseModel):
    """What the student gives: their idea + what they need."""
    research_question: str
    topic_summary: str
    discipline: str
    existing_knowledge: str = ""
    what_they_need: str = ""
    language: str = "en"

class ResearchPlan(BaseModel):
    """Output of HEAD planner: subquestions, search lanes, evidence needs, budget."""
    plan_id: str
    research_question: str
    subquestions: List[str] = Field(default_factory=list)
    strategy: str = ""  # broad_survey | targeted_gap_analysis | methodology_critique | full_pass
    search_lanes: List[Dict[str, str]] = Field(default_factory=list)
    evidence_needs: List[str] = Field(default_factory=list)
    budget_allocation: Dict[str, Any] = Field(default_factory=dict)
    suggested_methodology: str = ""

class LiteratureResult(BaseModel):
    """A single paper from a search, with verified metadata."""
    paper_id: str
    title: str
    authors: List[str]
    year: int
    abstract: str
    url: str
    source: str  # "arxiv" | "semantic_scholar"
    citation_count: int = 0
    extracted_claims: List[str] = Field(default_factory=list)

class LitMap(BaseModel):
    """Papers classified by relationship to student's idea."""
    research_question: str
    supporting: List[LiteratureResult] = Field(default_factory=list)
    challenging: List[LiteratureResult] = Field(default_factory=list)
    adjacent: List[LiteratureResult] = Field(default_factory=list)
    total_found: int
    search_query_used: str

class CritiqueResult(BaseModel):
    """Structured critique  -  never prose generation."""
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    gaps: List[str] = Field(default_factory=list)
    counterarguments: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    methodological_notes: List[str] = Field(default_factory=list)
    overall_assessment: str = ""

class SynthesisReport(BaseModel):
    """Extracted methods/datasets/metrics from papers, corpus comparisons."""
    research_question: str = ""
    method_summary: Dict[str, List[str]] = Field(default_factory=dict)
    dataset_summary: Dict[str, Any] = Field(default_factory=dict)
    metric_summary: Dict[str, Any] = Field(default_factory=dict)
    corpus_insights: Dict[str, List[str]] = Field(default_factory=dict)
    recommended_reading: List[Dict[str, str]] = Field(default_factory=list)
    cross_paper_comparisons: List[Dict[str, Any]] = Field(default_factory=list)

class CitationAudit(BaseModel):
    claims_checked: int
    verified_claims: int
    missing_citations: List[str] = Field(default_factory=list)
    weak_citations: List[str] = Field(default_factory=list)
    contested_claims: List[str] = Field(default_factory=list)
    bibtex_entries: Dict[str, str] = Field(default_factory=dict)

class ResearchSession(BaseModel):
    session_id: str
    research_context: ResearchContext
    research_plan: Optional[ResearchPlan] = None
    lit_map: Optional[LitMap] = None
    citation_audit: Optional[CitationAudit] = None
    synthesis_report: Optional[SynthesisReport] = None
    critique: Optional[CritiqueResult] = None
    created_at: str
    status: str = "complete"

class CorpusSection(BaseModel):
    section_id: str
    thesis_id: str
    thesis_title: str
    discipline: str
    university: str = ""
    year: int = 0
    section_type: str = ""  # introduction | methodology | literature_review | results | discussion | conclusion
    heading: str = ""
    text: str
    word_count: int = 0
    citation_count: int = 0

class CorpusStats(BaseModel):
    discipline: str
    thesis_count: int = 0
    section_stats: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

class CorpusContext(BaseModel):
    similar_sections: List[CorpusSection] = Field(default_factory=list)
    benchmarks: Optional[CorpusStats] = None

class Task(BaseModel):
    task_id: str
    task_type: str = ""
    description: str
    complexity_estimated: str  # e.g., low, medium, high
    status: str = "pending"
    research_context: Optional[ResearchContext] = None

class BlackboardEntry(BaseModel):
    entry_id: str
    entry_type: str  # task, evidence, output, route_decision
    content: Dict[str, Any]
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str

class EvidenceRef(BaseModel):
    evidence_id: str
    source: str
    summary: str
    confidence: float

class AgentOutput(BaseModel):
    agent_id: str
    tier: str
    result: Any
    confidence: float

class RouteDecision(BaseModel):
    strategy: str
    head_direct: bool = False
    workers_allowed: bool = False
    middle_allowed: bool = False
    rationale: str

class BudgetState(BaseModel):
    remaining_usd: float
    spent_usd: float
    token_limit: int

class EvalResult(BaseModel):
    passed: bool
    score: float
    feedback: str

class PrivacyPolicyDecision(BaseModel):
    privacy_tier: str  # public, sanitized, trusted
    is_blocked: bool
    reason: str

# Schema for compact routing episodes stored in memory
class EpisodeRoute(BaseModel):
    head_direct: bool = False
    used_middle_tier: bool = False
    used_worker_swarm: bool = False
    used_mcp_tools: List[str] = Field(default_factory=list)
    spawn_count: int = 0

class EpisodeModels(BaseModel):
    head: Optional[str] = None
    middle: List[str] = Field(default_factory=list)
    workers: List[str] = Field(default_factory=list)

class EpisodeMetrics(BaseModel):
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0

class EpisodeQuality(BaseModel):
    score: float = 0.0
    accepted: bool = False
    needed_retry: bool = False
    needed_head_correction: bool = False
    confidence: float = 0.0

class MemoryEpisode(BaseModel):
    """Compact routing episode schema to be stored in history."""
    episode_id: str
    timestamp: str
    task_summary: str
    task_type: str
    privacy_tier: str  # "public" | "sanitized" | "trusted"
    route: EpisodeRoute
    models: EpisodeModels
    metrics: EpisodeMetrics
    quality: EpisodeQuality
    failures: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    routing_takeaway: str = ""

class MemoryBrief(BaseModel):
    similar_past_tasks_count: int = 0
    strongest_successful_pattern: str = ""
    cheapest_acceptable_pattern: str = ""
    fastest_acceptable_pattern: str = ""
    common_failure_mode: str = ""
    recommended_routing_bias: str = ""
    confidence: str = "low"  # low, medium, high

    def to_formatted_string(self) -> str:
        return f"""MEMORY_ROUTING_BRIEF
- Similar past tasks: {self.similar_past_tasks_count}
- Strongest successful pattern: {self.strongest_successful_pattern}
- Cheapest acceptable pattern: {self.cheapest_acceptable_pattern}
- Fastest acceptable pattern: {self.fastest_acceptable_pattern}
- Common failure mode: {self.common_failure_mode}
- Recommended routing bias: {self.recommended_routing_bias}
- Confidence in memory signal: {self.confidence}"""
