# OpenWorkers

A thesis assistant — research partner, not a ghostwriter.
Searches real literature, critiques student ideas, verifies citations.
Built on a 3-tier hierarchical multi-agent system.

## Overview

The system helps bachelor/master students produce better theses by acting as a critical research partner. It does NOT write prose. Instead it:

- **Searches** verified literature via arXiv and Semantic Scholar APIs
- **Classifies** papers as supporting, challenging, or adjacent to the student's idea
- **Audits** citations — checks if cited papers exist and actually say what the student claims
- **Critiques** ideas and arguments with structured feedback (strengths, weaknesses, gaps, counterarguments)
- **Benchmarks** student work against a corpus of real theses (avg section lengths, citation density, common subsections)

**What it never does:**
- Write thesis sections or paragraphs
- Invent papers or citations
- Generate analysis without sources

## Architecture

```
STUDENT submits research question / idea / draft
         |
         v
  ┌──────────────┐
  │  WORKER:     │  Lit search via MCP tools (arXiv, Semantic Scholar)
  │  researcher  │  Returns verified papers with DOIs
  └──────┬───────┘
         v
  ┌──────────────┐
  │  MIDDLE:     │  Evidence verification + citation audit
  │  checker     │  Flags weak citations, contradictions
  └──────┬───────┘
         v
  ┌──────────────┐
  │  HEAD:       │  Structured critique: what's weak, what's missing,
  │  supervisor  │  counterarguments, suggestions
  └──────┬───────┘
         v
  STUDENT gets: lit map + critique + citation audit + corpus benchmarks
```

## Tech Stack
- **Python 3.12**, **Pydantic** — type-safe data models
- **Qdrant** + **FastEmbed** — episodic memory + thesis corpus
- **Redis** — shared state via blackboard
- **Anthropic / OpenAI / DeepSeek** — 3-tier agent providers
- **MCP tools** — arXiv, Semantic Scholar, CrossRef APIs
- **Docker Compose** & **pytest**

## Usage

### CLI (Phase 8)

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

### MCP Server (Phase 9)

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

### Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
make install
```

Copy `.env.example` to `.env`. Set `DRY_RUN=true` to run without API keys.

### Run

```bash
# CLI
python -m apps.cli.main research "your question" --format text

# Eval harness
python core/evals/thesis_harness.py
```

### Tests

```bash
pytest tests/ -v
```

## Implementation Roadmap

See [PHASES.md](PHASES.md) for the full build plan. Summary:

| Phase | What |
|---|---|
| 1 | Data models (ResearchContext, LitMap, CritiqueResult, etc.) |
| 2 | Academic MCP tools (arXiv, Semantic Scholar, CrossRef) |
| 3 | System prompt templates |
| 4 | Thesis agent providers |
| 5 | Orchestrator |
| 6 | Router update |
| 7 | Evaluation harness |
| 8 | CLI tool |
| 9 | MCP server (OpenCode + Claude Code) |
| 10 | Thesis corpus learning |

## Architecture

See [docs/architecture.md](docs/architecture.md) for the tier breakdown.
See [docs/examples.md](docs/examples.md) for output format samples.

## License

MIT — DavidHavoc, 2026
