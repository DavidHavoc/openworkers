<!-- generated-by: gsd-doc-writer -->
# Testing

This project uses [pytest](https://pytest.org) with `pytest-asyncio` for async tests, `fakeredis` for Redis-dependent tests, and `pytest-cov` for coverage reporting. All test files live in the `tests/` directory.

## Test framework and setup

| Tool | Version | Role |
|---|---|---|
| `pytest` | `>=8.0.0` | Test runner |
| `pytest-asyncio` | `>=0.23.0` | Async test support (`asyncio-mode=auto`) |
| `pytest-cov` | `>=5.0.0` | Coverage measurement |
| `fakeredis` | `>=2.20.0` | In-process Redis replacement |
| `mypy` | `>=1.10.0` | Static type checking |

Install all dev dependencies before running tests:

```bash
pip install -e ".[dev]"
```

## Running tests

Run the full test suite:

```bash
pytest
```

Run with verbose output:

```bash
pytest -v
```

Run a single test file:

```bash
pytest tests/test_cache.py
```

Run a single test by name:

```bash
pytest tests/test_core.py::test_memory_retrieval
```

Run with coverage reporting:

```bash
pytest --cov=. --cov-report=term
```

Generate an XML coverage report (used by CI):

```bash
pytest --cov=. --cov-report=term --cov-report=xml
```

The default `pytest` invocation already applies `-ra -q --asyncio-mode=auto` (defined in `pyproject.toml`) so flags like `-ra` and async support are always active without needing to pass them manually.

## Test files

| File | What it covers |
|---|---|
| `test_smoke.py` | FastAPI health check and task submission endpoints |
| `test_core.py` | Schemas, Blackboard, Router, EpisodicMemory, TaskOrchestrator, ThesisOrchestrator, SessionStore, parallelism invariants |
| `test_cache.py` | `SearchCache` key construction, Redis round-trips, TTL, error suppression; `MCPTool.execute` caching and tier enforcement |
| `test_resilience.py` | Error classification (`is_transient_error`), retry helper, circuit-breaker tripping and reset, `UnifiedLLM` fallback when preferred breaker is open |
| `test_budget.py` | `BudgetGuard` mechanics, `contextvars` scoping for concurrent sessions, `UnifiedLLM` provider skipping and budget recording |
| `test_rag.py` | Text chunking edge cases, `extract_text`, collection naming, `RAGIndexer`/`RAGSearchTool` round-trips, deduplication on re-ingest |
| `test_integrations.py` | MCP tool permissions, LLM adapter dry-run, structured output JSON validation, HTTP retry helper, eval harness |

## Singleton reset fixture (`conftest.py`)

`tests/conftest.py` defines a function-scoped `autouse` fixture `_reset_singletons` that runs before and after **every test**:

```python
@pytest.fixture(autouse=True)
def _reset_singletons() -> None:
    reset_settings(use_env_file=False)
    reset_default_cache()
    reset_default_registry()
    yield
    reset_settings(use_env_file=False)
    reset_default_cache()
    reset_default_registry()
```

This clears three process-wide singletons:

- `Settings` (from `core.config`) — reloads with `use_env_file=False` so tests are never affected by the developer's `.env` file
- `SearchCache` (from `tools.cache`) — resets the default cache client
- `ProviderBreakerRegistry` (from `providers.resilience`) — resets all circuit-breaker state

`monkeypatch.setenv` / `monkeypatch.delenv` are the only correct way to supply configuration to tests. Any settings set in a local `.env` file are intentionally invisible to the test suite.

## DRY_RUN mode

Tests that exercise the full thesis pipeline (LLM calls, orchestrator stages) use `DRY_RUN=true` to avoid hitting real provider APIs. Set it via `monkeypatch.setenv`:

```python
monkeypatch.setenv("DRY_RUN", "true")
```

When `DRY_RUN` is `true`, `LLMAdapter` returns placeholder text and placeholder JSON that satisfies Pydantic model schemas, allowing the full 8-stage pipeline to complete without any network calls. `test_smoke.py` sets `DRY_RUN` at module level because the FastAPI `TestClient` starts the app at import time.

CI runs the entire suite with `DRY_RUN=true` set as an environment variable so no API keys are required.

## fakeredis for Redis-dependent tests

Tests that require Redis use `fakeredis` to run an in-process Redis replacement. The standard pattern patches `redis.from_url` via `monkeypatch.setattr`:

```python
@pytest.fixture
def fake_redis(monkeypatch):
    server = fakeredis.FakeServer()
    fake = fakeredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(
        "redis.from_url",
        lambda *_args, **_kwargs: fakeredis.FakeRedis(server=server, decode_responses=True),
    )
    cache_module.reset_default_cache()
    yield fake
    cache_module.reset_default_cache()
```

Passing a shared `FakeServer` instance means all connections within a test see the same data, including TTL inspection via `fake_redis.ttl(key)` and key-count checks via `fake_redis.dbsize()`. `test_core.py` and `test_integrations.py` use a simpler inline fixture that does not yield the fake client.

## In-memory Qdrant (embedding cache)

`EpisodicMemory` and `RAGIndexer`/`RAGSearchTool` accept a `qdrant_location` parameter. All tests construct clients with `location=":memory:"` to avoid reading or writing to disk and to eliminate cross-test state:

```python
memory = EpisodicMemory(qdrant_location=":memory:")
```

```python
@pytest.fixture
def in_memory_client() -> QdrantClient:
    client = QdrantClient(location=":memory:")
    client.set_model(EMBEDDING_MODEL)
    return client
```

The `test_rag.py` module also clears `QDRANT_URL` from the environment at import time (`os.environ.pop("QDRANT_URL", None)`) to ensure the client cannot accidentally fall back to a remote Qdrant instance.

## Coverage requirements

Coverage is measured across all packages (`source = ["."]`), with the following omissions:

| Omitted path | Reason |
|---|---|
| `tests/*` | Test files themselves |
| `apps/mcp_server/*` | MCP server excluded from coverage gate |
| `scripts/*` | Utility scripts excluded |
| `.venv/*` | Virtualenv excluded |

The following code patterns are excluded from branch coverage:

- `if __name__ == "__main__":` guards
- `raise NotImplementedError`
- `def __repr__` and `def __str__`
- `Protocol` class bodies
- `@abstractmethod` decorated methods

**Minimum threshold:** `fail_under = 40` — pytest exits non-zero if line coverage falls below 40%.

Run the coverage check locally:

```bash
pytest --cov=. --cov-fail-under=40
```

## Static type checking (mypy)

`core/` and `providers/` packages are checked with `strict = true`:

```bash
mypy core/ --strict --ignore-missing-imports
mypy providers/ --strict --ignore-missing-imports
```

All other packages use relaxed settings (`strict = false`, `warn_return_any = true`). The mypy target version is Python 3.12 regardless of the runtime version used to run the suite.

## CI integration

Tests run in GitHub Actions on every push and pull request to `main` and `develop` (`.github/workflows/ci.yml`).

**Workflow: CI**

| Job | Trigger | Command |
|---|---|---|
| `lint` | push / PR to `main`, `develop` | `ruff check`, `black --check`, `flake8` |
| `typecheck` | push / PR to `main`, `develop` | `mypy core/ --strict`, `mypy providers/ --strict` |
| `test` | after `lint` passes | `pytest tests/ -v --cov=. --cov-report=term --cov-report=xml --cov-fail-under=40` |

The `test` job runs a matrix across Python **3.9** and **3.12**. The CI environment sets `DRY_RUN=true` so no provider API keys are needed. Coverage XML is uploaded to Codecov from the Python 3.12 matrix leg.

The `typecheck` job runs with `continue-on-error: true`, meaning mypy failures are reported but do not block the `test` job.
