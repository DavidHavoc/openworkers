<!-- generated-by: gsd-doc-writer -->
# Deployment

OpenWorkers ships as a single Docker image (`openworkers`) that supports three distinct runtime modes: a one-shot CLI, a stdio-based MCP server, and a FastAPI task-queue server. All three share the same image and the same environment variable contract described in [CONFIGURATION.md](CONFIGURATION.md).

---

## Prerequisites

- Docker >= 24 and Docker Compose (the `docker compose` plugin or standalone `docker-compose`)
- Python >= 3.9 (for local non-container runs; the image uses Python 3.12-slim)
- A `.env` file copied from `.env.example` with at least one LLM provider API key set and `DRY_RUN=false`

---

## Docker Compose Stack

`docker-compose.yml` defines four services and two named volumes.

### Services

| Service | Image | Profile | Purpose |
|---------|-------|---------|---------|
| `redis` | `redis:7-alpine` | _(default, always started)_ | Blackboard, session store, search cache |
| `qdrant` | `qdrant/qdrant:latest` | _(default, always started)_ | Vector store for episodic memory and RAG |
| `cli` | `openworkers` (built locally) | `tools` | One-shot CLI container (`thesis` command) |
| `mcp` | `openworkers` (built locally) | `tools` | Stdio MCP server (`python -m apps.mcp_server.main`) |

The `cli` and `mcp` services belong to the `tools` profile. They are not started by a bare `docker compose up` — you must opt in explicitly (see [Starting services](#starting-services) below).

### Volumes

| Volume | Mounted in | Purpose |
|--------|-----------|---------|
| `qdrant_data` | `qdrant` service at `/qdrant/storage` | Persists the Qdrant vector store across restarts |
| `fastembed_cache` | `mcp` service at `/root/.cache/huggingface` | Caches FastEmbed model weights so they are not re-downloaded |

The project source tree is also bind-mounted into both `cli` and `mcp` at `/app` (`- .:/app`), so changes to Python source take effect without a rebuild.

### Networking and ports

The Compose file exposes two ports on the host:

| Port | Service | Protocol |
|------|---------|---------|
| `6379` | `redis` | TCP (Redis) |
| `6333` | `qdrant` | HTTP (Qdrant REST/gRPC) |

Within the Compose network, `cli` and `mcp` reach Redis at `redis://redis:6379/0` and Qdrant at `http://qdrant:6333` — these values are injected via the `environment:` block and override any `.env` values.

The FastAPI server (`apps.api.main`) is not included in the Compose file. See [FastAPI task-queue server](#fastapi-task-queue-server) below for how to run it.

---

## Building and Starting Services

### Build the image

```bash
docker-compose build
# or via Makefile
make build
```

The image is built from the root `Dockerfile` (`python:3.12-slim`), installs the package in editable mode, and sets `DRY_RUN=false` as a build-time default. The image is tagged `openworkers` locally.

CI validates every build against this same Dockerfile (`docker` job in `.github/workflows/ci.yml`, triggered after tests pass).

### Start infrastructure only (Redis + Qdrant)

```bash
docker-compose up -d
# or
make up
```

This starts `redis` and `qdrant` only. Both include health checks; Redis will not report healthy until `redis-cli ping` succeeds.

### Stop all services

```bash
docker-compose down
# or
make down
```

Volumes are preserved. To also remove volumes: `docker-compose down -v`.

### View logs

```bash
docker-compose logs -f
# or
make logs
```

---

## Environment Variable Injection

Both `cli` and `mcp` services load environment variables from two sources in priority order:

1. **`environment:` block** in `docker-compose.yml` — highest priority. Sets `REDIS_URL` and `QDRANT_URL` to the Compose service hostnames.
2. **`env_file: .env`** — fallback for all other variables (LLM API keys, provider/model selection, `DRY_RUN`, etc.).

Copy `.env.example` to `.env` and fill in your API keys before starting any service:

```bash
cp .env.example .env
# edit .env: set ANTHROPIC_API_KEY / OPENAI_API_KEY / DEEPSEEK_API_KEY
# set THESIS_QUALITY_PROVIDER, THESIS_QUALITY_MODEL, etc.
# set DRY_RUN=false
```

The `.env` file must exist at the project root when running Docker Compose. Variables set directly in the host environment always take precedence over `.env` (standard pydantic-settings behaviour).

---

## The Three Runtimes

### 1. CLI one-shot (profile: `tools`)

The `cli` service runs the `thesis` CLI entrypoint (`apps.cli.main:main`). It exits after a single command completes. Use it to run research tasks, critique text, verify DOIs, search literature, manage sessions, or ingest documents into RAG collections.

```bash
# Run a research query and print results
docker-compose --profile tools run --rm cli thesis research "What is retrieval-augmented generation?"

# Verify a DOI
docker-compose --profile tools run --rm cli thesis verify "10.1145/3560815"

# List past sessions
docker-compose --profile tools run --rm cli thesis sessions
```

`redis` must be healthy before the `cli` container starts (`depends_on: redis: condition: service_healthy`). Start infrastructure first with `docker-compose up -d` if needed.

The `thesis` command can also be run **without Docker** if Python >= 3.9 and dependencies are installed:

```bash
pip install -e ".[dev]"
thesis research "Your question here"
```

### 2. MCP stdio server (profile: `tools`)

The `mcp` service runs `python -m apps.mcp_server.main`. It speaks the MCP protocol over stdin/stdout (`stdin_open: true`) and is intended to be launched by an MCP-compatible client (such as Claude Desktop or an IDE extension) rather than used interactively.

```bash
# Start the MCP server container (client connects via stdio)
docker-compose --profile tools run --rm mcp
```

The `mcp` service mounts `fastembed_cache` at `/root/.cache/huggingface` so the FastEmbed embedding model weights are only downloaded once and reused across container restarts.

The `thesis-mcp` entrypoint (defined in `pyproject.toml`) can also be invoked directly outside Docker:

```bash
thesis-mcp
```

### 3. FastAPI task-queue server

The FastAPI server (`apps/api/main.py`) is **not included in the Compose file** and must be started separately. It exposes an async task queue for submitting long-running research jobs over HTTP.

**Local / development:**

```bash
# Install dependencies first
pip install -e ".[dev]"

# Start the API server (auto-reload on code changes)
uvicorn apps.api.main:app --reload
# or
make dev
```

The API server requires Redis to be reachable at `REDIS_URL` (default: `redis://localhost:6379/0`). Start the Compose infrastructure first:

```bash
docker-compose up -d   # starts redis and qdrant
make dev               # starts FastAPI on localhost
```

**API endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check — returns status, tier, and pending task count |
| `POST` | `/tasks/` | Submit a research task (returns `202 Accepted` with `task_id`) |
| `GET` | `/tasks/{task_id}` | Poll task status and result |
| `GET` | `/tasks/` | List all tasks |
| `DELETE` | `/tasks/{task_id}` | Delete a task record |

Task state is held in-process memory (`_tasks` dict). **Task history does not survive a server restart.** For durable task storage, the session results are persisted by the session store (see [Session Persistence](#session-persistence) below).

<!-- VERIFY: default port for uvicorn when started with `make dev` or bare `uvicorn apps.api.main:app --reload` is 8000 -->

---

## Session Persistence

Sessions (research results, intermediate state) are persisted by the session store. The backend is selected via environment variables.

### Redis (default)

When `SESSION_BACKEND` is unset (and `DATABASE_URL` is also unset), sessions are stored in Redis under TTL-controlled keys.

```env
REDIS_URL=redis://localhost:6379/0
SESSION_TTL_SECONDS=2592000   # 30 days (default)
```

This is the default in Docker Compose. No additional setup is required beyond running the `redis` service.

### PostgreSQL

Set `SESSION_BACKEND=postgres` and provide a `DATABASE_URL` to switch to durable Postgres-backed sessions. Sessions stored this way survive Redis restarts and have no TTL.

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/openworkers
SESSION_BACKEND=postgres
```

Setting `DATABASE_URL` without `SESSION_BACKEND` also activates the Postgres backend automatically (the session store checks for `DATABASE_URL` presence).

The Docker Compose file does not include a `postgres` service. You must provide and manage the database instance separately.

<!-- VERIFY: database schema migrations or initialization steps required before first use with SESSION_BACKEND=postgres -->

---

## Qdrant: Embedded vs Remote

Qdrant stores embedding vectors for episodic memory and RAG collections.

### Embedded (default for local runs)

When `QDRANT_URL` is unset or empty, `qdrant-client` starts an in-process embedded Qdrant instance and writes data to `./qdrant_data` in the working directory.

```env
# QDRANT_URL=   (leave unset)
```

No separate process or container is needed. Suitable for development and single-user local deployments.

### Remote Qdrant container (Docker Compose default)

When `QDRANT_URL` is set, the client connects to a remote Qdrant instance. In Docker Compose, the `qdrant` service is always started and both `cli` and `mcp` point to it via the injected environment variable:

```env
QDRANT_URL=http://qdrant:6333
```

Data is persisted in the `qdrant_data` named volume (`/qdrant/storage` inside the container).

For production deployments pointing at a managed Qdrant instance, set `QDRANT_URL` to the remote endpoint in your deployment platform's secret manager.

<!-- VERIFY: Qdrant gRPC port (6334) availability and whether TLS/API key auth is required for managed Qdrant Cloud deployments -->

---

## CI/CD Pipeline

The CI workflow (`.github/workflows/ci.yml`) runs on push and pull requests to `main` and `develop` branches. It has four jobs:

| Job | Depends on | What it does |
|-----|-----------|-------------|
| `lint` | — | Runs ruff, black (check mode), and flake8 |
| `typecheck` | — | Runs mypy on `core/` and `providers/` (non-blocking: `continue-on-error: true`) |
| `test` | `lint` | Runs pytest with coverage (40% minimum) on Python 3.9 and 3.12; uploads XML report to Codecov |
| `docker` | `test` | Builds the Docker image via `docker/build-push-action` (build-only, no push) |

There is no automated push to a registry or deploy step in the CI pipeline. Deployment to production is a manual operation.

<!-- VERIFY: whether a separate nightly.yml workflow performs scheduled deployments or image pushes -->

---

## Production Deployment Checklist

Before deploying to a production environment:

1. Set `DRY_RUN=false` in your environment or secret manager.
2. Set `ENVIRONMENT=production` to enable JSON log output for log aggregators.
3. Set all required LLM API keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and/or `DEEPSEEK_API_KEY`).
4. Configure all three `THESIS_*_PROVIDER` and `THESIS_*_MODEL` variables.
5. Set `REDIS_URL` to a persistent Redis instance (not the default `localhost` address).
6. Decide on session persistence: set `SESSION_BACKEND=postgres` and `DATABASE_URL` for durable sessions, or leave unset to use Redis with TTL.
7. Set `QDRANT_URL` to a persistent remote Qdrant instance if embedding data must survive container replacement.
8. Optionally set `MAX_BUDGET_USD` to cap per-session LLM spend.
9. Optionally set `LOG_LEVEL=warning` or `error` to reduce log volume.

See [CONFIGURATION.md](CONFIGURATION.md) for the full variable reference.

<!-- VERIFY: specific cloud platforms (Fly.io, Railway, Render, AWS ECS, etc.) targeted for production deployment -->
<!-- VERIFY: whether a production Docker image is published to a container registry and under what tag scheme -->
