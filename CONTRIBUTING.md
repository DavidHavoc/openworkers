# Contributing

## Setup

See the [README](README.md) for install and `.env` configuration.

## Architecture

```
core/
├── schemas.py          Pydantic data models
├── blackboard/         Shared state (Redis)
├── memory/             Episodic memory (Qdrant)
├── router/             Agent + provider routing
├── orchestrator/       Task + thesis flow, prompt compiler
├── corpus/             Thesis corpus ingest/retrieve/benchmark
├── evals/              Thesis evaluation harness
├── observability/      Structured JSON logging
providers/
├── unified.py          Unified LLM routing layer
├── adapters.py         Provider backend adapters
├── thesis_agents.py    HEAD + 4 specialist agents
tools/mcp/
├── engine.py           Tool registry + base class
├── academic.py         arXiv, Semantic Scholar, CrossRef tools
prompts/                System prompt templates (Markdown)
apps/
├── cli/                CLI tool
├── mcp_server/         MCP JSON-RPC server
├── shared/             Formatting (text/JSON)
scripts/
├── seed_corpus.py      Bootstrap corpus from arXiv
```

## Adding a new provider

1. Add provider key to `ALL_PROVIDERS` in `providers/unified.py`
2. Add API key env var to `_PROVIDER_API_KEY_ENV` in `providers/adapters.py`
3. Add a branch to `LLMAdapter.generate()` for the new provider
4. Add a cost estimate to `COST_PER_1K_TOKENS`
5. Update `.env.example` with the new section

## Adding a new specialist agent

1. Create prompt template in `prompts/specialist_<name>.md`
2. Add template to `TEMPLATE_MAP` in `core/orchestrator/compiler.py`
3. Add compile method to `PromptCompiler`
4. Create agent class in `providers/thesis_agents.py`
5. Register agent in `ThesisOrchestrator.__init__` in `core/orchestrator/thesis_flow.py`
6. Add pipeline stage in `ThesisOrchestrator.execute()`

## Code conventions

- **Pydantic models** for all data structures  -  never pass raw dicts between agents
- **Async everywhere**  -  all agent execution and API calls use `async/await`
- **Stdlib for MCP tools**  -  `urllib` only, no extra HTTP dependencies
- **Errors surface in response**  -  pipeline stages catch exceptions and add to `errors` list; never crash
- **DRY_RUN**  -  all agents produce placeholder output when `DRY_RUN=true`. Test new features in DRY_RUN before needing real API keys
- **Logging**  -  use `obs_logger` for structured events; `logger.info/warning` for routing messages

## Testing

```bash
# Full test suite
pytest tests/ -v

# Smoke test
pytest tests/test_smoke.py -v

# Eval harness (DRY_RUN)
python -m core.evals.thesis_harness

# Specific command
python -m apps.cli.main research "test" --format json
```

## PR guidelines

- Run `pytest tests/ -v` and verify all tests pass
- Run `python -m core.evals.thesis_harness` and verify 7/7
- New modules need an `__init__.py`
- Follow existing class naming and docstring patterns
- No new dependencies without justification in the PR description
