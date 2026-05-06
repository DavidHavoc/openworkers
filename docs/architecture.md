# Architecture

## Thesis Pipeline Flow

```plantuml
@startuml
skinparam componentStyle rectangle
skinparam backgroundColor #FEFEFE

title Thesis Assistant Architecture

actor Student

package "Entry Points" {
    [CLI] as CLI
    [MCP Server] as MCP
    [API Server] as API
}

package "ThesisOrchestrator" {
    component "HEAD Planner" as H1
    component "HEAD Supervisor" as H2
    component "Researcher" as R
    component "Checker" as CH
    component "Synthesizer" as S
    component "Critic" as CR
}

package "Shared State" {
    database "Blackboard\n(Redis)" as BB
    database "Episodic Memory\n(Qdrant)" as EM
    database "Thesis Corpus\n(Qdrant)" as TC
}

package "UnifiedLLM" {
    component "Routing Layer" as RL
    component "Health Cache" as HC
    component "Budget Tracker" as BT
}

package "Providers" {
    [Anthropic\nSonnet/Haiku] as AN
    [OpenAI\nGPT-4o] as OA
    [DeepSeek\nChat] as DS
}

package "MCP Tools" {
    [arXiv\nSearch] as AR
    [Semantic\nScholar] as SS
    [CrossRef\nVerify] as XR
}

package "Output" {
    [ResearchSession] as OUT
}

Student --> CLI
Student --> MCP
Student --> API

CLI --> H1 : research
MCP --> H1 : thesis_research
API --> H1 : /tasks/

H1 --> EM : retrieve guidance
H1 --> BB : store plan

R --> AR : query
R --> SS : query
R --> BB : LitMap

CH --> XR : verify
CH --> BB : CitationAudit

S --> BB : SynthesisReport

TC --> S : similar sections
TC --> CR : benchmarks

CR --> BB : CritiqueResult

H2 --> BB : read all state
H2 --> BB : final critique

H1 --> RL : mode=quality
H2 --> RL : mode=quality
R --> RL : mode=cheap
CH --> RL : mode=balanced
S --> RL : mode=balanced
CR --> RL : mode=quality

RL --> HC : check health
RL --> BT : check budget
RL --> AN : fallback chain
RL --> OA
RL --> DS

BB --> OUT : assemble

@enduml
```

## Pipeline Stages

```
1. HEAD planner    → ResearchPlan (subquestions, search lanes, budget)
2. Memory          → MemoryBrief (similar past episodes bias routing)
3. Researcher      → LitMap (papers classified: supporting / challenging / adjacent)
4. Checker         → CitationAudit (verified, missing, weak, contested claims)
5. Synthesizer     → SynthesisReport (methods, datasets, metrics, corpus comparisons)
6. Critic          → CritiqueResult (strengths, weaknesses, gaps, counterarguments)
7. HEAD supervisor → final CritiqueResult (merges all findings, assesses viability)
8. Assemble        → ResearchSession (wraps everything, stores episode in memory)
```

## Routing Layer

The `UnifiedLLM` routing layer maps agent mode to provider + model via env vars:

| Mode | Agents | Env Var | Controls |
|---|---|---|---|
| `quality` | HEAD planner, HEAD supervisor, critic | `THESIS_QUALITY_PROVIDER` / `_MODEL` | Provider + model |
| `balanced` | checker, synthesizer | `THESIS_BALANCED_PROVIDER` / `_MODEL` | Provider + model |
| `cheap` | researcher | `THESIS_CHEAP_PROVIDER` / `_MODEL` | Provider + model |

**Fallback:** If a provider fails or has no API key, the next available provider is tried. Health checks are cached for 60 seconds.

## Observability

All pipeline stages emit structured JSON via `obs_logger`: session start, stage completion, failures, memory hits, budget traces.

## Security Tiers

- **Public**: MCP tools, web search
- **Sanitized**: Structured data lookups
- **Trusted**: No external API calls, HEAD-only resolution
