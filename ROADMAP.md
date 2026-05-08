# Roadmap

> **Status legend:** ✅ shipped · 🚧 in progress · 📋 proposed · ❄️ deferred / icebox
>
> Everything below the **Shipped (0.1.0)** section is **not yet implemented**. This file captures the direction we plan to take, not the state of the code today. PRs that pull any 📋 item into 🚧 / ✅ are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## Shipped (0.1.0)

- ✅ Hierarchical agent pipeline (HEAD planner → researcher → checker → synthesizer → critic → HEAD supervisor)
- ✅ Pydantic v2 schemas for every agent output (`ResearchPlan`, `LitMap`, `CitationAudit`, `SynthesisReport`, `CritiqueResult`)
- ✅ Provider-agnostic LLM router (Anthropic, OpenAI, DeepSeek) with quality / balanced / cheap tiers, health cache, fallback chains
- ✅ Real literature tools — arXiv, Semantic Scholar, CrossRef — with shared httpx client and exponential-backoff retry
- ✅ Redis blackboard, Qdrant episodic memory, thesis-corpus benchmarks
- ✅ CLI (`thesis research / critique / verify / papers / corpus / sessions / resume`)
- ✅ MCP server exposing four tools over stdio
- ✅ Session persistence (Redis with optional Postgres backend) with discipline/status filters
- ✅ FastAPI surface with task submission and result polling
- ✅ Evaluation harness covering routing correctness and search recall
- ✅ Docker Compose stack (Redis, Qdrant, CLI, MCP) and CI matrix on Python 3.9 / 3.12
- ✅ **RAG over user PDFs** — `thesis ingest add paper.pdf --collection my_papers` chunks + embeds via FastEmbed (BAAI/bge-small-en-v1.5) into Qdrant; the researcher transparently retrieves from the user collection when `thesis research ... --rag-collection my_papers` is set. Collections are namespaced under `rag_*` so they cannot collide with the thesis corpus or episodic memory.

## Proposed for 1.0

The 1.0 line targets a polished, packaged release on PyPI. The themes:

### Packaging & developer ergonomics
- 📋 Move source tree to `src/openworkers/` and ship as `pip install openworkers`
- 📋 Replace the argparse CLI with **Typer + Rich** (better help, completion, colored output, progress spinners)
- 📋 Replace ad-hoc env var reads with **pydantic-settings** `Settings` (one typed source of truth)
- 📋 Bump minimum Python to **3.11** for `Self`, `LiteralString`, `tomllib`, exception groups
- 📋 Provide a **uv lockfile** for reproducible installs

### Resilience
- 📋 **Tenacity** retries with exponential jitter on transient provider errors
- 📋 **pybreaker** circuit breakers per provider (open after N failures, auto-reset)
- 📋 **Hard budget guard** — pre-call estimation aborts before exceeding `MAX_BUDGET_USD`
- 📋 **Parallel-safe stages** — researcher + corpus retrieval run via `asyncio.gather`; checker + synthesizer also parallel after the lit map is in
- 📋 Resumable / durable orchestration via Temporal or Prefect (likely 1.x, not 1.0)

### Extensibility
- 📋 **Provider registry** — `@register_provider("ollama")` decorator + entry-point discovery; first-class **Ollama**, **Together**, **Groq**, and generic OpenAI-compatible support
- 📋 **Tool registry** — every literature source is a class registered via entry points; users add their own without forking
- ✅ **RAG over user PDFs** — *shipped in 0.1.x*; see the Shipped section above. Future work: `add` accepts directories, optional reranker, and an `--exclude-source` flag.
- 📋 **Versioned prompt library** — Jinja2 templates under `prompts/v1/`, addressable as `prompt://name@version`, with an A/B harness scored by the critic

### Observability
- 📋 **Structlog** with JSON output in production, Rich tracebacks in development
- 📋 **OpenTelemetry** spans for every agent step, every LLM call, every tool call (OTLP / Jaeger)
- 📋 **Prometheus** metrics at `/metrics`: per-provider latency histograms, token counts, cost gauges, cache hit ratio, fallback counter

### MCP & editor integration
- 📋 **JSON-RPC 2.0** compliance — proper error codes, batch requests, streaming `progress` notifications during long pipelines, optional bearer-token auth
- 📋 Shipped configs for **Cursor, Windsurf, Continue.dev, Claude Desktop, OpenCode, Claude Code**
- 📋 Better tool descriptions (example inputs, expected outputs, latency budgets) so LLM clients pick the right tool first time

### Performance & cost
- 📋 Redis-backed cache for arXiv/SS/CrossRef searches (24h TTL by default)
- 📋 Diskcache + sqlite for the FastEmbed embedding cache (survives container restarts)
- 📋 Smart truncation: blackboard-to-prompt compilation deduplicates and ranks entries by relevance before injection
- 📋 Early termination — supervisor short-circuits the pipeline if the plan is judged unsalvageable

### Documentation & release engineering
- 📋 **MkDocs Material** site (`mkdocs.yml`) with versioned docs, search, and Mermaid rendering
- 📋 GitHub Actions release workflow: tag → PyPI + GHCR Docker image
- 📋 MkDocs deployment to GitHub Pages
- 📋 Property-based tests for the LLM-JSON repair logic via Hypothesis
- 📋 VCR-py cassettes for arXiv / SS / CrossRef so integration tests run offline

## Beyond 1.0

- ❄️ Optional **HTMX web UI** with SSE streaming of agent updates
- ❄️ **Disagreement-driven multi-runs** — researcher executed by two different LLMs in parallel; if they disagree on >X% of claims, escalate
- ❄️ **Live citation graph** — Semantic Scholar API → second-degree neighbours, identify "missing keystone" papers via PageRank
- ❄️ **Self-eval loop** — every session emits an EvalEpisode that feeds the harness baselines; score regressions auto-trigger prompt-A/B PRs
- ❄️ **Browser plugin** — Chrome/Firefox extension that sends the active arXiv / SS / journal page to OpenWorkers for instant critique
- ❄️ **Multi-user mode** — JWT auth, per-user RAG collections, S3-backed corpus, Postgres session store; one-line deploy to Fly.io / Railway

## How to influence the roadmap

Open an issue with the `roadmap` label, or comment on an existing one. We particularly want feedback from researchers actively writing theses or systematic reviews — the priorities above are best-guess, and ground truth from real users will reorder them.
