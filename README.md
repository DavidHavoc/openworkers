# OpenWorkers

[![CI](https://github.com/DavidHavoc/openworkers/actions/workflows/ci.yml/badge.svg)](https://github.com/DavidHavoc/openworkers/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12-blue.svg)](#install)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE-MIT)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Lint: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

A multi-agent thesis assistant that searches real literature, audits citations, and produces structured critiques. **It does not write prose.** It runs a hierarchical pipeline (HEAD planner → researcher → checker → synthesizer → critic → HEAD supervisor) over a Redis blackboard, with provider-agnostic LLM routing across Anthropic, OpenAI, and DeepSeek and verified citations via arXiv, Semantic Scholar, and CrossRef.

> **Project status:** 0.1.0 (pre-release). The pipeline runs end-to-end and ships an MCP server, a CLI, and a FastAPI app, but APIs may shift before 1.0. See [ROADMAP.md](ROADMAP.md) for the planned 1.0 direction.

## What it does

| # | Capability | Notes |
|---|-----------|-------|
| 1 | **Literature Map** | arXiv + Semantic Scholar; classifies results as supporting / challenging / adjacent |
| 2 | **Citation Audit** | Flags missing, weak, contested citations across the lit set |
| 3 | **Synthesis Report** | Methods, datasets, metrics; cross-paper comparisons |
| 4 | **Structured Critique** | Strengths, weaknesses, gaps, counterarguments, suggestions — JSON, never prose |
| 5 | **Corpus Benchmarks** | Ingest thesis PDFs; compare your section length and citation density to a reference corpus |
| 6 | **Idea/Draft Critique** | Standalone critique without running a full pipeline |
| 7 | **Citation Verification** | DOI lookup via CrossRef; returns metadata or reports it does not exist |
| 8 | **Quick Paper Search** | arXiv / Semantic Scholar by keyword — no LLM, no token cost |
| 9 | **Session Persistence** | Resume past sessions; list and filter by discipline/status (Redis or Postgres) |
| 10 | **Multi-Provider Router** | quality / balanced / cheap tiers with health checks, fallback chains, budget tracking |
| 11 | **Privacy Tiers** | public / sanitized / trusted gate which data sources each agent can access |
| 12 | **JSON Output** | Every command supports `--format json` and `--output file.json` |
| 13 | **MCP Server** | Four tools over stdio for Claude Code, OpenCode, and any MCP-aware client |
| 14 | **Evaluation Harness** | Built-in tests for routing correctness, search recall, fake-DOI detection |
| 15 | **Dockerized** | Compose stack with Redis, Qdrant, CLI runner, and MCP service |

## Architecture

```mermaid
flowchart LR
    User([CLI / MCP / FastAPI]) --> Orch[Thesis Orchestrator]
    Orch --> Plan[HEAD Planner]
    Plan --> Res[Researcher]
    Res --> arXiv[arXiv]
    Res --> SS[Semantic Scholar]
    Res --> BB[(Blackboard<br/>Redis)]
    Plan -.-> BB
    BB --> Check[Checker]
    Check --> XR[CrossRef]
    Check --> BB
    BB --> Synth[Synthesizer]
    Synth --> BB
    BB --> Crit[Critic]
    Crit --> BB
    BB --> Sup[HEAD Supervisor]
    Sup --> Out[ResearchSession]
    Orch -.->|every LLM call| Router[UnifiedLLM<br/>quality / balanced / cheap]
    Router --> Anthropic & OpenAI & DeepSeek
    Sup -.-> Mem[(Episodic Memory<br/>Qdrant)]
    Out -.-> Store[(Session Store<br/>Redis or Postgres)]
```

Full pipeline-stage table and routing detail in [docs/architecture.md](docs/architecture.md).

## Install

Python 3.12 is recommended (the test matrix runs 3.9 and 3.12). Redis is required at runtime; Qdrant runs embedded by default.

```bash
git clone https://github.com/DavidHavoc/openworkers.git
cd openworkers
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Configure

Copy `.env.example` to `.env` and pick **one** provider configuration. A minimal single-provider setup:

```env
DEEPSEEK_API_KEY=sk-...
THESIS_QUALITY_PROVIDER=deepseek
THESIS_QUALITY_MODEL=deepseek-chat
THESIS_BALANCED_PROVIDER=deepseek
THESIS_BALANCED_MODEL=deepseek-chat
THESIS_CHEAP_PROVIDER=deepseek
THESIS_CHEAP_MODEL=deepseek-chat
DRY_RUN=false
```

The three modes route different agents to different models so you can pay strong-model rates only where they matter:

| Mode | Used by | Suggested model class |
|------|---------|-----------------------|
| `quality` | HEAD planner, HEAD supervisor, critic | strongest |
| `balanced` | checker, synthesizer | mid |
| `cheap` | researcher | cheap / fast |

`DRY_RUN=true` runs the full pipeline without any API keys — useful for tests, demos, and CI.

## Docker

```bash
docker compose build
docker compose up -d redis qdrant
docker compose run --rm cli python -m apps.cli.main research "your question"
```

`cli` and `mcp` services live behind the `tools` profile and start on demand. `.env` is mounted automatically.

## CLI

```bash
thesis research "Can light replace electrons in CPUs?" --discipline computer_science
thesis critique "Social media causes depression because teens spend too much time online"
thesis verify "10.1038/nature14539"
thesis papers "transformer attention" --source arxiv --limit 5
thesis corpus ingest thesis.pdf --title "My Thesis" --discipline cs --year 2024
thesis sessions
thesis resume <session-id>
```

Every command accepts `--format json` and `--output path/to/file.json`. See [docs/examples.md](docs/examples.md) for full sample outputs.

## MCP Server (Claude Code, OpenCode, and other MCP clients)

The server exposes four tools over stdio: `thesis_research`, `thesis_critique`, `thesis_verify_citation`, `thesis_search_papers`.

**Claude Code** — add to `~/.claude/mcp.json` or a project-level `.mcp.json`:

```json
{
  "mcpServers": {
    "thesis-assistant": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["-m", "apps.mcp_server.main"]
    }
  }
}
```

**OpenCode** — add to `~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "thesis-assistant": {
      "type": "local",
      "command": [
        "bash", "-lc",
        "cd /absolute/path/to/openworkers && docker compose run --rm -i mcp"
      ]
    }
  }
}
```

Replace `/absolute/path/to/openworkers` with your local checkout path. Conversation examples live in [docs/examples.md](docs/examples.md).

## How OpenWorkers compares

OpenWorkers is for *researching* a thesis, not writing one. The closest analogues are research-discovery tools, not generic chat:

| Capability                          | OpenWorkers | Generic LLM Chat | Elicit | scite | Connected Papers |
|-------------------------------------|:-----------:|:----------------:|:------:|:-----:|:----------------:|
| Refuses to write prose for you      | ✅          | ❌               | partial| n/a   | n/a              |
| arXiv + Semantic Scholar search     | ✅          | ❌               | ✅     | ✅    | ✅               |
| CrossRef DOI verification           | ✅          | ❌               | partial| ✅    | ❌               |
| Structured critique (JSON schema)   | ✅          | ❌               | ❌     | ❌    | ❌               |
| Corpus benchmarking from your PDFs  | ✅          | ❌               | ❌     | ❌    | ❌               |
| Multi-provider LLM routing          | ✅          | ❌               | ❌     | ❌    | ❌               |
| MCP / editor integration            | ✅          | n/a              | ❌     | ❌    | ❌               |
| Self-hostable, MIT-licensed         | ✅          | ❌               | ❌     | ❌    | ❌               |

## Contributing

Bug fixes, docs, features, and provider integrations welcome. Before opening a PR, run:

```bash
pytest tests/ -v
ruff check . && black --check .
mypy core/ providers/ --strict --ignore-missing-imports
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow.

## License

[MIT](LICENSE-MIT).
