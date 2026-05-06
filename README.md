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

The thesis assistant runs as a pipeline: HEAD plans the work → specialists generate structured artifacts on a shared blackboard → HEAD reviews and critiques the assembled output.

```
Student → CLI/MCP → HEAD Planner → [Blackboard]
                                  → Researcher (lit search)
                                  → Checker (citation audit)
                                  → Synthesizer (methods, corpus benchmarks)
                                  → Critic (counterarguments, gaps)
                                  → HEAD Supervisor (final review)
                                  → ResearchSession
```

All agents call through a single UnifiedLLM router that maps quality/balanced/cheap modes to Claude, ChatGPT, or DeepSeek. MCP tools (arXiv, Semantic Scholar, CrossRef) run without LLM involvement. Thesis corpus benchmarks make critiques data-driven \: "your methodology is 200 words; CS theses average 1,100."

See [docs/architecture.md](docs/architecture.md) for the full Mermaid diagram and pipeline stage details.

## Tech Stack
- **Python 3.9+**, **Pydantic**  -  data models and structured output parsing
- **Redis**  -  blackboard shared state
- **Qdrant** + **FastEmbed**  -  episodic memory and thesis corpus
- **UnifiedLLM**  -  provider-agnostic routing (Claude, ChatGPT, DeepSeek)
- **MCP tools**  -  arXiv, Semantic Scholar, CrossRef (stdlib HTTP, no LLM)
- **pymupdf**  -  PDF extraction for corpus ingest

## Usage

### CLI

```bash
# Full research session
thesis research "your question" --discipline computer_science

# Critique an idea
thesis critique "Social media causes depression"

# Verify a citation (DOI)
thesis verify "10.1038/nature14539"

# Quick paper search, no LLM
thesis papers "transformer attention" --source arxiv --limit 5

# Ingest a thesis into the corpus
thesis corpus ingest "thesis.pdf" --title "My Thesis" --discipline cs --year 2024
```

See [docs/examples.md](docs/examples.md) for full output samples.

### MCP Server

The MCP server talks JSON-RPC over stdin/stdout. Add it to OpenCode's config (global: `~/.config/opencode/opencode.json` or project root: `opencode.json`):

```json
{
  "mcp": {
    "thesis-assistant": {
      "type": "local",
      "command": ["docker", "compose", "run", "--rm", "-i", "mcp"]
    }
  }
}
```

Four tools are registered: `thesis_research`, `thesis_critique`, `thesis_verify_citation`, `thesis_search_papers`.

For Claude Code:

```bash
claude mcp add thesis-assistant -- python -m apps.mcp_server.main
```

Your `.env` (with API keys and `DRY_RUN=false`) must be present. Redis and Qdrant need to be running — if using Docker, `docker compose up -d redis qdrant` handles that.

### API Server

A FastAPI stub is available at `apps/api/main.py`. It provides a `/health` endpoint and a `/tasks/` stub  -  not wired to the full pipeline yet. Useful as a foundation or for health-check monitoring:

```bash
uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
```

Then `curl http://localhost:8000/health` returns `{"status":"ok"}`.

## Setup

**Prerequisites:** Python 3.9+, Redis (for blackboard state). Qdrant runs embedded  -  no server needed.

### Install

```bash
git clone https://github.com/DavidHavoc/openworkers.git
cd openworkers
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

### Configure `.env`

Copy and edit:

```bash
cp .env.example .env
```

Set your API key and per-mode provider + model. Example for DeepSeek:

```env
DEEPSEEK_API_KEY=sk-...
DRY_RUN=false
THESIS_QUALITY_PROVIDER=deepseek
THESIS_QUALITY_MODEL=deepseek-chat
THESIS_BALANCED_PROVIDER=deepseek
THESIS_BALANCED_MODEL=deepseek-chat
THESIS_CHEAP_PROVIDER=deepseek
THESIS_CHEAP_MODEL=deepseek-chat
```

The three modes let different agents use different models:

| Mode | Agents |
|---|---|
| `quality` | HEAD planner, HEAD supervisor, critic |
| `balanced` | checker, synthesizer |
| `cheap` | researcher |

Same provider with different models per mode works (Claude Sonnet for quality, Haiku for cheap). Mixed providers also fine.

**Dry run**  -  set `DRY_RUN=true` to skip API calls and test without keys.

### Docker

```bash
docker compose build
docker compose up -d redis qdrant           # background services
docker compose run --rm cli python -m apps.cli.main research "your question"
docker compose run --rm cli python -m apps.cli.main verify "10.1038/nature14539"
```

The CLI and MCP services use a `tools` profile  -  they run on demand rather than auto-starting with `up`. Your `.env` is mounted automatically.

### Tests

```bash
pytest tests/ -v

# Thesis eval harness (7 tests)
python -m core.evals.thesis_harness
```

See [docs/examples.md](docs/examples.md) for detailed output samples.

## Contributing

We welcome contributions from the community: bug fixes, documentation improvements, new features, or provider integrations. Before submitting a PR, run the test suite and eval harness to make sure nothing breaks. See [CONTRIBUTING.md](CONTRIBUTING.md) for architecture overview, code conventions, and the PR checklist.

## License

MIT  -  DavidHavoc, 2026
