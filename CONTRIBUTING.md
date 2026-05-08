# Contributing to OpenWorkers

Thanks for taking the time to contribute. This guide covers setup, the workflow we expect, and the conventions enforced in CI.

## Getting started

```bash
git clone https://github.com/DavidHavoc/openworkers.git
cd openworkers
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

Copy `.env.example` to `.env` and configure API keys for whichever providers you want to test against live. To work without any keys, set `DRY_RUN=true` — the full pipeline runs end-to-end with placeholder LLM responses, which is what CI uses.

## Running locally

```bash
make test                              # pytest -ra -q --asyncio-mode=auto
make lint                              # black + flake8
ruff check .                           # fast linting
mypy core/ providers/ --strict --ignore-missing-imports

# Full pipeline in dry-run mode
DRY_RUN=true python -m apps.cli.main research "Test research question"

# Stack with Redis + Qdrant
docker compose up -d redis qdrant
docker compose run --rm cli python -m apps.cli.main research "..."
```

## Submitting changes

1. Branch from `main`: `git checkout -b feat/short-description main`
2. Make focused commits — one logical change per commit when reasonable.
3. Run the full check set:
   - `pytest tests/ -v`
   - `ruff check . && black --check .`
   - `mypy core/ providers/ --strict --ignore-missing-imports`
4. Use Conventional Commit prefixes (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`).
5. Push your branch and open a PR against `main`. Fill out the PR template; link any issue you're closing.
6. CI must be green before review. Pre-commit must be clean (`pre-commit run --all-files`).

## Code standards

- **Python 3.9+** — code must run on the lowest version in our test matrix. Use `from __future__ import annotations` if you need newer typing syntax.
- **Pydantic v2** for every data model — no raw dicts in business logic, no manual JSON parsing where a Pydantic model already exists.
- **Async-first** — all I/O (HTTP, Redis, Postgres, LLM calls) goes through `async/await`. We use `httpx` with shared connection pools, never `requests` or `urllib`.
- **Structured LLM output** — every agent call uses an API-level JSON schema (Anthropic `tool_use`, OpenAI `json_schema`, DeepSeek `json_object`). Don't introduce free-form prose paths.
- **Ruff** for import sorting and linting; **Black** for formatting (line length 100); **Mypy strict** for `core/` and `providers/`.
- **No prose generation.** OpenWorkers critiques and audits — it does not draft thesis paragraphs. PRs that turn it into a writer will be rejected.

## Project structure

```
apps/          entry points (CLI, FastAPI, MCP server, worker stub)
core/          orchestrator, router, blackboard, memory, schemas, evals, sessions
providers/     LLM adapters, agent implementations, unified routing
tools/         MCP tool implementations (academic search, web search)
prompts/       LLM prompt templates per agent role
tests/         pytest suite
scripts/       seed corpus, utilities
docs/          architecture and example transcripts
```

## Testing

- Unit tests in `tests/test_core.py` cover schemas, blackboard, routing, memory.
- Integration tests in `tests/test_integrations.py` cover MCP tools, adapters, the eval harness.
- Smoke tests in `tests/test_smoke.py` cover the FastAPI surface.

All tests use `fakeredis` for Redis and an in-memory Qdrant client. **No external services or API keys are required to run the suite** — `DRY_RUN=true` is the default for tests.

If you add a new provider, tool, or agent, please:

1. Add a unit test that exercises the happy path.
2. Add a dry-run test that proves it doesn't accidentally hit the network without `DRY_RUN=false`.
3. Update `docs/architecture.md` if you've added a stage, route, or output shape.

## Reporting bugs / requesting features

Use the issue templates under `.github/ISSUE_TEMPLATE/`. For security issues, see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for contact.
