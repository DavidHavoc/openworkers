# Contributing to OpenWorkers

## Getting started

```bash
git clone https://github.com/DavidHavoc/openworkers.git
cd openworkers
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Set up pre-commit hooks:

```bash
pre-commit install
```

Copy `.env.example` to `.env` and configure API keys for providers you want to test live.
`DRY_RUN=true` runs the full pipeline without making LLM calls.

## Running locally

```bash
make test       # Run all tests
make lint       # Run black + flake8
ruff check .    # Fast linting with ruff

# Run the full thesis pipeline in dry-run mode
python -m apps.cli.main research "Test research question"

# Build and run with Docker
docker compose build
docker compose up -d
```

## Submitting changes

1. Create a branch from `develop`: `git checkout -b feat/my-feature develop`
2. Make your changes
3. Run tests: `pytest tests/ -v`
4. Run linters: `ruff check . && black --check . && flake8 .`
5. Run type checks: `mypy core/ providers/ --strict --ignore-missing-imports`
6. Commit with a descriptive message following conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`)
7. Push and open a PR against `develop`

## Code standards

- **Python 3.9+** compatible
- **Pydantic v2** for all data models — no raw dicts or JSON strings in the business logic
- **Async-first** — all I/O uses `async/await` via httpx
- **Structured output** — all LLM responses are validated through Pydantic schemas
- **Ruff** handles import sorting, basic linting, and formatting
- **Black** is the formatter (line length 100)
- **Mypy strict** is required for `core/` and `providers/` modules

## Project structure

```
apps/          -- entry points (CLI, API, MCP server, worker)
core/          -- orchestrator, router, blackboard, memory, schemas, evals
providers/     -- LLM adapters, agent implementations, unified routing
tools/         -- MCP tool implementations (academic search, web search)
tests/         -- pytest suite
prompts/       -- LLM prompt templates
scripts/       -- seed corpus, utilities
```

## Testing

- Unit tests in `tests/test_core.py` — schemas, blackboard, routing, memory
- Integration tests in `tests/test_integrations.py` — MCP tools, adapters, evals
- Smoke tests in `tests/test_smoke.py` — API endpoints

All tests use `fakeredis` for Redis and in-memory Qdrant. No external services required for test runs.
