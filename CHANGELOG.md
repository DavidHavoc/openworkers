# Changelog

All notable changes to OpenWorkers are documented here.

## [0.1.0] — Unreleased

### Added
- Thesis data models: ResearchPlan, LitMap, CitationAudit, SynthesisReport, CritiqueResult
- Academic MCP tools: arXiv search, Semantic Scholar, CrossRef verification
- LLM prompt templates for HEAD planner, supervisor, and 4 specialist agents
- Unified LLM routing with provider fallback, health cache, budget tracking
- Anthropic, OpenAI, and DeepSeek provider adapters
- Thesis orchestrator with 8-stage pipeline (planner → researcher → checker → synthesizer → corpus → critic → supervisor)
- Router with privacy-tier gating, budget-based agent activation, per-mode provider mapping
- Episodic memory backed by Qdrant with similarity-based routing guidance
- Blackboard (Redis) for inter-agent shared state
- Evaluation harness for routing correctness
- CLI (`thesis research`, `thesis critique`, `thesis verify`, `thesis papers`)
- MCP server for OpenCode and Claude Code integration
- Thesis corpus ingestion, analysis, and section retrieval
- Docker Compose dev environment (API, worker, Postgres, Redis, Qdrant)

### Fixed (Phase 1)
- Router `provider_map` defaults: modes without env vars now fall back to sensible provider/model pairs
- Route fallback chain: `provider_fallback` dict with ordered provider lists per mode
- Blackboard `_get_entries()` infinite recursion
- Structured output: all 5 agent output models use API-level JSON schemas (Anthropic `tool_use`, OpenAI `json_schema`, DeepSeek `json_object`)
- HTTP: migrated from `urllib.request` to `httpx` with shared connection pools and exponential-backoff retry
- 33 tests covering schemas, routing, blackboard, memory, adapters, tools, retry, and full pipeline

### Engineering (Phase 2)
- CI/CD: GitHub Actions for lint, typecheck, test matrix (3.9/3.12), Docker build
- Nightly integration workflow for live API tests behind secrets
- Pre-commit hooks: trailing-whitespace, YAML/JSON validation, ruff, black, flake8, mypy
- Mypy strict mode for `core/` and `providers/`
- Coverage threshold at 60% with pytest-cov
- Contribution guide, code of conduct, PR template, issue templates
