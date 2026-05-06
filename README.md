# OpenWorkers

A thesis assistant - research partner, not a ghostwriter.
Searches real literature, critiques student ideas, verifies citations.
Built on a multi-agent system with unified LLM routing.

## Overview

The system helps bachelor/master students produce better theses by acting as a critical research partner. It does NOT write prose. Instead:

1. **HEAD supervisor (first pass)** - understands the goal, plans research strategy, defines subquestions, sets budgets, decides the route
2. **4 specialist agents** - researcher (lit search), checker (citation audit, contradiction detection), synthesizer (methods/dataset extraction, corpus benchmarking), critic (counterarguments, weakness analysis)
3. **HEAD supervisor (final pass)** - merges findings, critiques the draft, decides confidence, produces student output

Outputs delivered to the student:
- Literature map with DOI-verified papers
- Citation audit (verified claims, missing/weak citations, contradictions)
- Structured critique (strengths, weaknesses, gaps, counterarguments, next steps)
- Corpus benchmarks (section lengths, citation density, common subsections vs real theses)
- Methodology and dataset summaries extracted from literature

**What it never does:**
- Write thesis sections or paragraphs
- Invent papers or citations
- Generate analysis without sources

## Architecture

```
STUDENT
  submits: research question, draft, claim to verify, topic to map
        |
        v
┌──────────────────────────────┐
│ API / Session Layer          │
│ - request intake             │
│ - auth/session               │
│ - task creation              │
└──────────────┬───────────────┘
               v
┌──────────────────────────────┐
│ HEAD SUPERVISOR  (first pass)│
│ - understands goal           │
│ - plans research strategy    │
│ - defines subquestions       │
│ - sets budgets               │
│ - decides route              │
└───────┬───────────┬──────────┘
        |           |
        |           v
        |    ┌──────────────────────┐
        |    │ MEMORY LAYER         │
        |    │ - episodic memory    │
        |    │ - route outcomes     │
        |    │ - failure patterns   │
        |    └──────────┬───────────┘
        |               |
        v               v
┌────────────────────────────────────┐
│ ROUTING / POLICY LAYER             │
│ - provider-agnostic model routing  │
│ - privacy checks                   │
│ - budget checks                    │
│ - confidence thresholds            │
│ - fallback logic                   │
└───────┬──────────────┬─────────────┘
        |              |
        |              v
        |      ┌────────────────────┐
        |      │ MCP TOOL LAYER     │
        |      │ - arXiv            │
        |      │ - Semantic Scholar │
        |      │ - Crossref         │
        |      │ - local corpus     │
        |      │ - notes / files    │
        |      └────────────────────┘
        |
        v
┌─────────────────────────────────────────────────────┐
│ SPECIALIST AGENTS                                   │
│                                                     │
│ 1. Researcher                                       │
│    - search papers, collect metadata, retrieve      │
│      abstracts and sources                          │
│                                                     │
│ 2. Checker                                          │
│    - verify citations, detect contradictions,       │
│      flag weak evidence                             │
│                                                     │
│ 3. Synthesizer                                      │
│    - extract methods / datasets / metrics           │
│    - corpus comparison (section lengths, etc)       │
│                                                     │
│ 4. Critic                                           │
│    - counterarguments, missing work, weakness       │
│      analysis, alternative framings                 │
└──────────────────────┬──────────────────────────────┘
                       v
┌────────────────────────────────────┐
│ SHARED STATE / BLACKBOARD          │
│ - paper IDs, DOI refs              │
│ - evidence refs, quality scores    │
│ - contradictions, route trace      │
└──────────────────────┬─────────────┘
                       v
┌────────────────────────────────────┐
│ HEAD SUPERVISOR  (final pass)      │
│ - merges findings                  │
│ - critiques draft                  │
│ - decides confidence               │
│ - creates student output           │
└──────────────────────┬─────────────┘
                       v
STUDENT OUTPUT
- literature map
- verified citations
- contradiction warnings
- benchmark / dataset summary
- critique of argument
- what is missing
- next reading suggestions
```

## Tech Stack
- **Python 3.12**, **Pydantic** - type-safe data models, structured LLM output parsing
- **httpx** - async HTTP for MCP tools (arXiv, Semantic Scholar, CrossRef)
- **Qdrant** + **FastEmbed** - episodic memory + thesis corpus
- **Redis** - shared state via blackboard
- **Unified LLM Interface** - routing layer with policy engine, fallback, budget controls, health checks
- **Provider Adapters** - Anthropic, OpenAI, DeepSeek (pluggable)
- **MCP tools** - arXiv, Semantic Scholar, CrossRef APIs
- **Docker Compose** & **pytest** (19 tests)

## Usage

### CLI (Phase 9)

```bash
# Full research session
thesis research "How do sparse attention mechanisms compare to dense ones?"

# Critique an idea or draft section
thesis critique "My methodology uses a within-subjects design with 20 participants"

# Verify a citation
thesis verify "Smith 2023 found that attention is quadratic in sequence length"

# Quick paper search (no LLM, pure API)
thesis papers "transformer attention mechanisms" --limit 10

# Resume a previous session
thesis resume SESSION_ID

# Ingest a thesis into the corpus
thesis corpus ingest "path/to/thesis.pdf" --discipline computer_science --year 2024
```

### MCP Server (Phase 10)

Add to OpenCode or Claude Code:

```json
{
  "mcpServers": {
    "thesis-assistant": {
      "command": "python",
      "args": ["-m", "apps.mcp_server.main"],
      "cwd": "/path/to/openworkers"
    }
  }
}
```

Then use natively in conversation: *"Find papers on sparse attention and critique my research question."*

## local development

### Prerequisites

- Python 3.9+
- Redis (for blackboard shared state)
- Qdrant runs embedded via `./qdrant_data` — no separate server needed

### Install

```bash
git clone https://github.com/DavidHavoc/openworkers.git
cd openworkers
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

### Configure `.env`

Copy the example and open it:

```bash
cp .env.example .env
```

**Required** — pick ONE provider section, uncomment it, fill in real values:

```env
# API keys (set the one matching your chosen provider)
ANTHROPIC_API_KEY=sk-ant-...

# Per-mode routing (uncomment one block)
DRY_RUN=false

# Single Anthropic (set real model names)
THESIS_QUALITY_PROVIDER=anthropic
THESIS_QUALITY_MODEL=claude-sonnet-4-20250514
THESIS_BALANCED_PROVIDER=anthropic
THESIS_BALANCED_MODEL=claude-haiku-4-5-20250514
THESIS_CHEAP_PROVIDER=anthropic
THESIS_CHEAP_MODEL=claude-haiku-4-5-20250514
```

The three modes control which model each agent uses:

| Mode | Agent | Typical model |
|---|---|---|
| `quality` | HEAD planner, HEAD supervisor, critic | stronger model |
| `balanced` | checker, synthesizer | mid model |
| `cheap` | researcher | cheaper/faster model |

Same provider with different models is fine (Anthropic Sonnet for quality, Haiku for balanced/cheap). Mixed providers also work.

**Dry run** — set `DRY_RUN=true` to skip API calls and test the pipeline locally with placeholder output. No API keys needed. The routing layer logs what it *would* have called.

### Run

```bash
# Full research session
python -m apps.cli.main research "your question" --discipline computer_science

# Output as JSON
python -m apps.cli.main research "your question" --format json

# Save to file
python -m apps.cli.main research "your question" --output session.json

# Critique only
python -m apps.cli.main critique "Social media causes depression"

# Verify a citation
python -m apps.cli.main verify "10.1038/nature14539"

# Quick paper search (no LLM, pure API)
python -m apps.cli.main papers "transformer attention mechanisms" --source arxiv --limit 5

# Eval harness
python core/evals/thesis_harness.py
```

### Tests

```bash
pytest tests/ -v
```

## Implementation Roadmap

| Phase | What |
|---|---|
| 1 | Data models (ResearchContext, LitMap, CritiqueResult, etc.) |
| 1-fix | **Done** — Bug fixes: router provider fallback defaults, blackboard recursion, httpx migration, structured JSON parsing |
| 2 | Academic MCP tools (arXiv, Semantic Scholar, CrossRef) |
| 3 | System prompt templates (HEAD planner/supervisor + 4 specialists) |
| 4 | Unified LLM Interface (routing layer, policy, fallback, budget) |
| 5 | 4 specialist agent providers (researcher, checker, synthesizer, critic) |
| 6 | Orchestrator (HEAD plan -> specialists -> HEAD final) |
| 7 | Router / policy layer integration |
| 8 | Evaluation harness |
| 9 | CLI tool |
| 10 | MCP server (OpenCode + Claude Code) |
| 11 | Thesis corpus learning |

See [docs/architecture.md](docs/architecture.md) for the tier breakdown.
See [docs/examples.md](docs/examples.md) for output format samples.

## License

MIT — DavidHavoc, 2026
