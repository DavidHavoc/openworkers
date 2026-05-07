# OpenWorkers

A thesis assistant: searches real literature, critiques ideas, verifies citations. Does not write prose. Built on a multi-agent pipeline with provider-agnostic LLM routing (DeepSeek, Claude, ChatGPT), designed to be accurate and cost-effective.

## Capabilities

| # | Capability | What it does |
|---|-----------|--------------|
| 1 | **Literature Map** | Searches arXiv + Semantic Scholar, classifies papers as supporting / challenging / adjacent, all with verified DOIs |
| 2 | **Citation Audit** | Checks which claims have sources; flags missing, weak, or contested citations across the literature |
| 3 | **Synthesis Report** | Extracts methods, datasets, metrics from papers; cross-paper comparisons; consensus findings and knowledge gaps |
| 4 | **Structured Critique** | Strengths, weaknesses, gaps, counterarguments citing specific papers, actionable suggestions for hardening your research |
| 5 | **Corpus Benchmarks** | Ingest thesis PDFs to build a discipline-specific corpus; compare your section length and citation density against successful theses |
| 6 | **Idea/Draft Critique** | Standalone critique of any claim, idea, or draft section without running a full literature search |
| 7 | **Citation Verification** | Check if a DOI is real via CrossRef API; returns verified metadata (title, authors, year, publisher) or reports it doesn't exist |
| 8 | **Quick Paper Search** | Search arXiv or Semantic Scholar by keyword — no LLM involved, no token cost; returns papers with verified IDs and citation counts |
| 9 | **Session Management** | Resume prior research sessions from memory; list all past sessions |
| 10 | **Multi-Provider LLM Router** | Provider-agnostic routing across DeepSeek, Claude, ChatGPT with health checks, fallback chains, budget tracking, and three quality tiers (quality / balanced / cheap) |
| 11 | **Privacy Tiers** | Public / sanitized / trusted tiers gate which data sources each agent can access |
| 12 | **JSON Output** | All commands support `--format json` and `--output file.json` for programmatic use and piping |
| 13 | **MCP Server** | Exposes 4 tools over stdin/stdout for direct integration inside OpenCode and Claude Code editors |
| 14 | **Evaluation Harness** | 7 built-in tests covering search recall, fake DOI detection, bad idea detection, cost measurement, synthesis quality, and full pipeline integrity |
| 15 | **Dockerized** | Docker Compose with Redis, Qdrant, CLI runner, and MCP server services |

## Install

```bash
git clone https://github.com/DavidHavoc/openworkers.git
cd openworkers
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Needs Python 3.9+, Redis. Qdrant runs embedded.

## Configure

Copy `.env.example` to `.env`. Set your API key and per-mode routing:

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

Three modes let different agents use different models:

| Mode | Agents | Use a |
|---|---|---|
| `quality` | HEAD planner, HEAD supervisor, critic | stronger model |
| `balanced` | checker, synthesizer | mid model |
| `cheap` | researcher | cheaper/faster model |

Set `DRY_RUN=true` to test without API keys.

## Docker

```bash
docker compose build
docker compose up -d redis qdrant
docker compose run --rm cli python -m apps.cli.main research "your question"
```

Services start on demand via a `tools` profile. Your `.env` mounts automatically.

## CLI

```bash
thesis research "can light replace electrons in CPUs?" --discipline computer_science
thesis critique "Social media causes depression because teens spend too much time online"
thesis verify "10.1038/nature14539"
thesis papers "transformer attention" --source arxiv --limit 5
thesis corpus ingest "thesis.pdf" --title "My Thesis" --discipline cs --year 2024
thesis resume <session-id>
thesis sessions
```

`--format json` and `--output file.json` supported. See [docs/examples.md](docs/examples.md) for full output samples.

## MCP Server (OpenCode / Claude Code)

The server exposes four tools over stdin/stdout: `thesis_research`, `thesis_critique`, `thesis_verify_citation`, `thesis_search_papers`.

**OpenCode** - add to `~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "thesis-assistant": {
      "type": "local",
      "command": [
        "bash", "-lc",
        "cd /Users/David/Documents/GitHub/openworkers && docker compose run --rm -i mcp"
      ]
    }
  }
}
```

**Claude Code** - add to `~/.claude/mcp.json` or `.mcp.json` in your project:

```json
{
  "mcpServers": {
    "thesis-assistant": {
      "command": "bash",
      "args": ["-lc", "cd /path/to/openworkers && docker compose run --rm -i mcp"]
    }
  }
}
```

Or without Docker, pointing directly at your venv:

```json
{
  "mcpServers": {
    "thesis-assistant": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "apps.mcp_server.main"]
    }
  }
}
```

See [docs/examples.md](docs/examples.md) for conversation examples inside OpenCode and Claude Code.

## Architecture

```
Student -> CLI/MCP -> HEAD Planner -> Blackboard (Redis)
                                   -> Researcher (lit search)
                                   -> Checker (citation audit)
                                   -> Synthesizer (methods, benchmarks)
                                   -> Critic (counterarguments, gaps)
                                   -> HEAD Supervisor (final review)
                                   -> ResearchSession
```

All agents call through a single `UnifiedLLM` router mapping `quality`/`balanced`/`cheap` modes to your chosen provider. MCP tools (arXiv, Semantic Scholar, CrossRef, DuckDuckGo) run API calls directly - no LLM involved. Full Mermaid diagram in [docs/architecture.md](docs/architecture.md).

## Contributing

Bug fixes, docs, features, provider integrations welcome. Run `pytest tests/ -v` and `python -m core.evals.thesis_harness` before submitting a PR. See [CONTRIBUTING.md](CONTRIBUTING.md) for conventions and architecture overview.

## License

MIT — DavidHavoc, 2026
