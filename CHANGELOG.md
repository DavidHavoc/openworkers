# Changelog

All notable changes to OpenWorkers are documented here. The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Code-audit track — PR auditor (second slice).** New `openworkers audit pr <github-url>` CLI subcommand verifies a pull request description against its actual diff. Same pipeline shape as the README auditor (planner → deterministic researcher → checker + trust gate → critic), with two new layers: `core/sources/github.py` (`GitHubAdapter` implementing `SourceAdapter` over a unified diff, plus `PrSpec` value object, `parse_pr_url`, `fetch_pr_from_github` for live GitHub fetch with optional `GITHUB_TOKEN`/`GH_TOKEN`, and `load_pr_fixture` for offline tests) and `core/orchestrator/pr_flow.py` (`PrAuditOrchestrator`). New agents `PrPlannerAgent` and `PrCheckerAgent` in `providers/code_audit_agents.py`; `AuditCriticAgent` reused as-is. New prompts `prompts/code_audit/pr_planner.md` + `pr_checker.md` with PR-specific claim types (`add | remove | fix | refactor | test | behavior | doc | other`) and diff-aware verdict rules. Audit-prompt rendering extracted from `readme_flow.py` into `core/orchestrator/audit_prompts.py` so each new auditor can register templates without touching unrelated modules. `tests/fixtures/sample_pr/` contains a canned PR (json + unified diff) with verified / drifted / contradicted / fabricated claims; `tests/code_audit/test_pr_flow.py` asserts verdict distribution and an explicit trust-gate override.
- **Code-audit track — README auditor (first slice).** New `openworkers audit readme <repo>` CLI subcommand verifies every factual claim in a README against the actual repository, emitting `verified | drifted | unsupported | contradicted` verdicts with cited file paths. Pipeline: planner (LLM) → researcher (deterministic grep via new `LocalRepoAdapter`) → checker (LLM + post-LLM trust gate) → critic (LLM adversarial pass). New modules: `core/sources/` (`SourceAdapter` ABC + `LocalRepoAdapter`), `core/schemas_audit.py` (Pydantic audit models), `core/orchestrator/readme_flow.py` (`ReadmeAuditOrchestrator`), `providers/code_audit_agents.py` (planner / checker / critic + `_enforce_trust_gate` invariant), `prompts/code_audit/*.md` (audit templates). The trust gate is enforced in code, not delegated to prompts: any claim with no retrieved evidence is forced to `unsupported` regardless of LLM output. The audited README is excluded from its own evidence pool so fabricated claims cannot self-verify. `tests/code_audit/test_readme_flow.py` exercises the full flow with a stubbed `UnifiedLLM.generate_fn` and an `tests/fixtures/sample_repo/` containing a deliberate mix of verified / drifted / contradicted / fabricated claims. Thesis pipeline untouched.
- **Contributor onboarding doc** `AGENTS.md` capturing project DNA, code-audit slice design, trust-gate invariant, conventions, and the recipe for adding new auditors.
- **RAG over user PDFs** (first incremental v1.0 slice). New `tools/mcp/rag.py` with sentence-aware chunker, `RAGIndexer` (PDF/text → Qdrant via PyMuPDF + FastEmbed `BAAI/bge-small-en-v1.5`), and `RAGSearchTool` (registered as `rag_search` in `ToolRegistry`). Collections namespaced under `rag_*` so they cannot collide with `thesis_corpus` or `episodes`. New CLI: `thesis ingest add|list|delete`. New flag: `thesis research ... --rag-collection <name>` makes the researcher pull from the user collection alongside arXiv/SS. New field: `ResearchContext.rag_collection`. `tests/test_rag.py` covers chunking edge cases, BOM/text extraction, collection naming, indexer round-trip, privacy gating, and idempotent re-ingest.

### Documentation
- README rewritten with badges, Mermaid architecture diagram, comparison table, and accurate MCP setup instructions (replaced the hardcoded `/Users/David/...` example path with a placeholder).
- Clarified Python version support (3.9+ supported, 3.12 used in CI and Docker).
- CONTRIBUTING.md updated to branch from `main` (the `develop` branch referenced previously did not exist) and to reflect the actual lint/test commands enforced in CI.
- Added [ROADMAP.md](ROADMAP.md) describing the proposed v1.0 direction (`src/openworkers/` package layout, Ollama provider, RAG over user PDFs, MkDocs site, JSON-RPC 2.0 MCP improvements). All items are explicitly labelled as **proposed**, not shipped.
- docs/architecture.md polished and cross-linked from README.

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
- Docker Compose stack with Redis, Qdrant, a CLI runner service, and an MCP service (gated behind the `tools` profile)

### Fixed (Phase 1)
- Router `provider_map` defaults: modes without env vars now fall back to sensible provider/model pairs
- Route fallback chain: `provider_fallback` dict with ordered provider lists per mode
- Blackboard `_get_entries()` infinite recursion
- Structured output: all 5 agent output models use API-level JSON schemas (Anthropic `tool_use`, OpenAI `json_schema`, DeepSeek `json_object`)
- HTTP: migrated from `urllib.request` to `httpx` with shared connection pools and exponential-backoff retry
- 37 tests covering schemas, routing, blackboard, memory, adapters, tools, retry, sessions, and full pipeline

### Engineering (Phase 2)
- CI/CD: GitHub Actions for lint, typecheck, test matrix (3.9/3.12), Docker build
- Nightly integration workflow for live API tests behind secrets
- Pre-commit hooks: trailing-whitespace, YAML/JSON validation, ruff, black, flake8, mypy
- Mypy strict mode for `core/` and `providers/` — zero errors across 26 source files
- Coverage threshold at 40% with pytest-cov (48% actual)
- Contribution guide, code of conduct, PR template, issue templates

### Added (Session persistence)
- `core/sessions/store.py`: async `BaseSessionStore` interface
- `RedisSessionStore`: sessions stored as JSON in Redis with 30-day TTL, sorted-set index
- `PgSessionStore`: Postgres via asyncpg with auto-created `sessions` table (JSONB data column, indexes on discipline/status/created_at)
- `create_session_store()`: auto-selects Postgres when `DATABASE_URL` is set, Redis otherwise
- `ThesisOrchestrator` auto-saves completed `ResearchSession` after Stage 8
- CLI: `thesis sessions` lists past sessions with `--discipline` and `--status` filters
- CLI: `thesis resume <id>` reloads full session including plan, lit map, and critique
- Tests: 4 async tests covering save/load, list/count, delete, and pipeline auto-save
- Total: 37 tests passing

### Fixed
- MCP timeout: retries reduced from 3 to 2, per-tool `read_timeout` (20s arXiv/SS, 15s CrossRef), total deadline cap prevents runaway retry loops
- Ruff CI: limited to E/W/F/I selectors, E501 excluded (line-length handled by black)
- Mypy CI: `follow_imports` removed, `python_version` bumped to 3.12 for match/case syntax in dependencies
- Type errors: `Optional` annotations, `-> None`, `dict`/`list` type args, `no_implicit_optional` violations fixed across 17 files
- Black: all 50 files conform to line-length 100
