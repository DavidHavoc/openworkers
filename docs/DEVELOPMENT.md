<!-- generated-by: gsd-doc-writer -->
# Development Guide

This guide covers everything needed to work on the OpenWorkers codebase: environment setup, code style enforcement, project layout, and patterns for adding new providers or tools.

---

## Dev environment setup

**Requirements:** Python 3.9 or later (tested on 3.9 and 3.12).

```bash
git clone https://github.com/DavidHavoc/openworkers.git
cd openworkers

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e ".[dev]"            # installs runtime + dev extras (pytest, ruff, black, mypy, fakeredis, …)

pre-commit install                 # wire up the pre-commit hooks
```

Copy `.env.example` to `.env` and fill in the API keys for the providers you want to test against:

```bash
cp .env.example .env
# edit .env — set at minimum one of ANTHROPIC_API_KEY, OPENAI_API_KEY, or DEEPSEEK_API_KEY
```

To work **without any API keys** — for running tests, iterating on logic, or exploring the pipeline — set `DRY_RUN=true`. The full pipeline executes end-to-end but LLM calls return deterministic placeholder responses. This is the default for the test suite and for CI.

---

## Build and dev commands

| Command | What it does |
|---|---|
| `make install` | `pip install -e ".[dev]"` (repeatable, safe to re-run) |
| `make dev` | Start the FastAPI server with hot-reload (`uvicorn apps.api.main:app --reload`) |
| `make test` | Run the full pytest suite |
| `make lint` | Run `black .` then `flake8 .` |
| `make build` | Build the Docker image |
| `make up` | `docker-compose up -d` (Redis + Qdrant + all services) |
| `make down` | Stop all Docker services |
| `make logs` | Tail Docker Compose logs |

For the infrastructure services only (without the app container):

```bash
docker compose up -d redis qdrant
```

---

## Code style

The project enforces three tools on every commit via pre-commit hooks and in CI.

### Ruff

Fast linting and import sorting. Config lives in `pyproject.toml` under `[tool.ruff]`.

- Line length: 100 characters
- Enabled rule sets: `E`, `W`, `F` (pycodestyle + pyflakes), `I` (isort), `B` (bugbear), `C4` (comprehensions), `UP` (pyupgrade), `SIM` (simplify)
- `E501` (line-too-long) is suppressed — Black handles line length

```bash
ruff check .                   # lint only
ruff check . --fix             # lint + auto-fix safe issues
```

### Black

Opinionated formatter. Config lives in `pyproject.toml` under `[tool.black]`.

- Line length: 100
- Target versions: `py39`

```bash
black .                        # format in-place
black --check --diff .         # CI mode: report diffs without writing
```

### Mypy

Type checking with **strict mode** applied specifically to `core/` and `providers/`. The rest of the codebase runs with relaxed settings.

Config lives in `pyproject.toml` under `[tool.mypy]`. The per-module override:

```toml
[[tool.mypy.overrides]]
module = ["core.*", "providers.*"]
strict = true
ignore_missing_imports = true
```

```bash
mypy core/ providers/ --strict --ignore-missing-imports
```

The `typecheck` CI job runs the same command. It is set to `continue-on-error: true` so a type error is surfaced as a warning rather than blocking the test job, but the pre-commit hook treats mypy failures as hard errors.

### Running all checks at once

```bash
pre-commit run --all-files
```

This runs the full hook stack: trailing whitespace, YAML/JSON/TOML validity, large-file guard, private-key detection, ruff (with auto-fix), Black (check mode), flake8 (fatal syntax errors only), and mypy on `core/` + `providers/`.

---

## Project layout

```
apps/        Entry points — CLI, FastAPI server, MCP server, worker stub.
             Nothing in apps/ is imported by other packages.

core/        Orchestrator, router, blackboard, memory, schemas, evals, sessions.
             The domain heart of the system. Mypy strict is enforced here.
             Key files:
               core/schemas.py          — all Pydantic v2 domain models
               core/config.py           — pydantic-settings singleton (get_settings())
               core/orchestrator/       — flow compiler and thesis/generic flows
               core/blackboard/         — shared state store (Redis-backed)
               core/memory/             — episodic memory (Qdrant-backed)
               core/router/             — LLM routing logic
               core/evals/              — evaluation harness

providers/   LLM adapters and agent implementations. Mypy strict is enforced here.
             Key files:
               providers/base.py        — BaseAgentProvider ABC
               providers/interfaces.py  — HeadProvider / MiddleProvider / WorkerProvider
               providers/adapters.py    — LLMAdapter (wraps Anthropic, OpenAI, DeepSeek)
               providers/unified.py     — UnifiedLLM (routes across adapters)
               providers/thesis_agents.py — concrete agent implementations

tools/       MCP tool implementations. Each tool subclasses MCPTool from tools/mcp/engine.py.
             tools/mcp/academic.py      — arXiv + Semantic Scholar search
             tools/mcp/rag.py           — RAG retrieval tool
             tools/cache.py             — Redis-backed search result cache

prompts/     LLM prompt templates, one file per agent role. Plain text or Jinja2.

tests/       Pytest suite (fakeredis + in-memory Qdrant, no real API calls by default).
               tests/test_core.py        — schemas, blackboard, routing, memory
               tests/test_integrations.py — MCP tools, adapters, eval harness
               tests/test_smoke.py       — FastAPI surface

scripts/     Seed corpus utilities and one-off helpers. Not imported by the main packages.

docs/        Architecture overview, configuration reference, examples.
```

---

## DRY_RUN=true: local iteration without API keys

`DRY_RUN` is the primary mechanism for working on the pipeline without spending API credits or requiring provider credentials.

When `DRY_RUN=true` (the default in `core/config.py`):

- `LLMAdapter.generate()` returns a deterministic JSON stub after a 10 ms artificial delay.
- No HTTP connections to Anthropic, OpenAI, or DeepSeek are attempted. Client objects are not instantiated.
- The full orchestration pipeline — planning, literature mapping, synthesis, critique, citation audit — runs and produces output that can be used to test routing, blackboard state, schema validation, and agent sequencing.

To run the pipeline locally in dry-run mode:

```bash
# CLI entry point
DRY_RUN=true python -m apps.cli.main research "What are the limitations of RAG for scientific literature?"

# Or set it in your .env — DRY_RUN=true is already the default so you only need
# to override if you want live LLM calls:
echo "DRY_RUN=false" >> .env
```

The test suite never sets `DRY_RUN=false`. If you write a test that exercises a live provider path, gate it with `pytest.mark.live` or put it in `test_integrations.py` where it runs only under the nightly workflow (which injects real keys from GitHub Secrets).

---

## Adding a new LLM provider

1. **Add the adapter** in `providers/adapters.py`. Instantiate the client only when `not self.dry_run and self.api_key`. Return a stub string from `generate()` when `self.dry_run is True`.

2. **Register the API key attribute** in the `_PROVIDER_API_KEY_ATTR` dict at the top of `providers/adapters.py`:

   ```python
   _PROVIDER_API_KEY_ATTR = {
       "anthropic": "anthropic_api_key",
       "openai":    "openai_api_key",
       "deepseek":  "deepseek_api_key",
       "myprovider": "myprovider_api_key",   # add this
   }
   ```

3. **Add the settings field** in `core/config.py` under the `# API keys` block:

   ```python
   myprovider_api_key: str = ""
   ```

4. **Update `.env.example`** with the new key and instructions.

5. **Add unit tests.** At minimum: a happy-path test and a dry-run test proving no network calls go out when `DRY_RUN=true`.

6. **Mypy must pass.** `providers/` is strict — annotate every parameter and return type.

---

## Adding a new tool

Tools live in `tools/mcp/` and subclass `MCPTool` from `tools/mcp/engine.py`. Each tool must implement three abstract methods:

```python
from tools.mcp.engine import MCPTool
from typing import Any

class MyTool(MCPTool):
    name = "my_tool"
    description = "One-sentence description exposed to the MCP client."
    allowed_tiers: list[str] = ["public", "sanitized", "trusted"]
    cacheable = True   # set True if results can be cached in Redis

    def get_input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }

    def get_output_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"results": {"type": "array"}}}

    async def execute_impl(self, params: dict[str, Any]) -> dict[str, Any]:
        # I/O goes here. Use httpx with the shared client pattern from academic.py.
        ...
```

The `execute()` wrapper on the base class enforces privacy-tier access control, Redis caching (when `cacheable = True`), and audit logging. You do not call `execute_impl()` directly in production code.

After implementing the tool:

1. Register it with the MCP server in `apps/mcp_server/`.
2. Add a unit test in `tests/test_integrations.py` covering the happy path and a dry-run/network-isolation path.
3. Update `docs/architecture.md` if the tool introduces a new data flow or external dependency.

---

## Branch conventions and PR process

Branch from `main`:

```bash
git checkout -b feat/short-description main
# or: fix/issue-123, docs/update-contributing, refactor/router-cleanup, test/add-smoke
```

Use [Conventional Commit](https://www.conventionalcommits.org/) prefixes: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`.

Before opening a PR, the full check set must pass locally:

```bash
pytest tests/ -v
ruff check . && black --check .
mypy core/ providers/ --strict --ignore-missing-imports
pre-commit run --all-files
```

The PR template (`CONTRIBUTING.md` checklist) requires all four checks to be green, tests added or updated, and documentation updated if the change affects architecture, configuration, or public APIs. CI must be green before review begins.

---

## CI overview

| Workflow | Trigger | Jobs |
|---|---|---|
| `ci.yml` | Push/PR to `main` or `develop` | `lint` → `test` (Python 3.9 + 3.12) → `docker` build |
| `nightly.yml` | Daily at 03:00 UTC + manual dispatch | Live API integration tests, Docker smoke test |

The `test` job runs with `DRY_RUN=true` and no API keys. Coverage must stay above 40% (`--cov-fail-under=40`). The `typecheck` job runs mypy on `core/` and `providers/` with `--strict` but is non-blocking (`continue-on-error: true`).

The nightly workflow uses real provider keys injected from GitHub Secrets and runs `tests/test_integrations.py` and `tests/test_smoke.py` against live APIs with `DRY_RUN=false`.
