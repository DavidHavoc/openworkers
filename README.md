# OpenWorkers

A thesis assistant for students — searches real literature, critiques ideas, verifies citations. Does not write prose. Built on a multi-agent pipeline with provider-agnostic LLM routing (DeepSeek, Claude, ChatGPT).

## What it does

Submit a research question. The system searches academic databases, classifies papers, audits citations, extracts methods and metrics, benchmarks against a thesis corpus, and produces a structured critique with counterarguments and suggestions.

| Output | What's in it |
|---|---|
| Literature Map | Papers classified as supporting / challenging / adjacent, all with verified DOIs |
| Citation Audit | Which claims have sources, which are missing, weak, or contested across the literature |
| Synthesis Report | Methods, datasets, metrics extracted from papers; cross-paper comparisons |
| Critique | Strengths, weaknesses, gaps, counterarguments citing specific papers, actionable suggestions |
| Corpus Benchmarks | How your section length and citation density compare to successful theses |

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
```

`--format json` and `--output file.json` supported. See [docs/examples.md](docs/examples.md) for full output samples.

## MCP Server (OpenCode / Claude Code)

The server exposes four tools over stdin/stdout: `thesis_research`, `thesis_critique`, `thesis_verify_citation`, `thesis_search_papers`.

**OpenCode** — add to `~/.config/opencode/opencode.json`:

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

**Claude Code** — add to `~/.claude/mcp.json` or `.mcp.json` in your project:

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

All agents call through a single `UnifiedLLM` router mapping `quality`/`balanced`/`cheap` modes to your chosen provider. MCP tools (arXiv, Semantic Scholar, CrossRef) run API calls directly — no LLM involved. Full Mermaid diagram in [docs/architecture.md](docs/architecture.md).

## Contributing

Bug fixes, docs, features, provider integrations welcome. Run `pytest tests/ -v` and `python -m core.evals.thesis_harness` before submitting a PR. See [CONTRIBUTING.md](CONTRIBUTING.md) for conventions and architecture overview.

## License

MIT — DavidHavoc, 2026
