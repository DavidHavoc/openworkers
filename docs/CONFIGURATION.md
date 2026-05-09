<!-- generated-by: gsd-doc-writer -->
# Configuration

OpenWorkers reads all configuration from environment variables (with a `.env` file as fallback). The authoritative list is the `Settings` class in `core/config.py`, which uses pydantic-settings. Unknown environment variables are silently ignored. Variable names are case-insensitive.

Copy `.env.example` to `.env` before first run:

```bash
cp .env.example .env
```

---

## LLM Providers

The routing system has three quality tiers. Each tier is configured independently with a provider name and a model name. You need at least one API key set; the system errors at startup if a provider is selected but has no key.

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | If used | `""` | API key for the Anthropic provider. |
| `OPENAI_API_KEY` | If used | `""` | API key for the OpenAI provider. |
| `DEEPSEEK_API_KEY` | If used | `""` | API key for the DeepSeek provider. |
| `THESIS_QUALITY_PROVIDER` | Yes | `anthropic` | Provider for the quality tier (`anthropic`, `openai`, or `deepseek`). Used by: HEAD planner, HEAD supervisor, critic agent. |
| `THESIS_QUALITY_MODEL` | Yes | `claude-sonnet-4-20250514` | Model name for the quality tier. Must be a model name supported by the chosen provider. |
| `THESIS_BALANCED_PROVIDER` | Yes | `openai` | Provider for the balanced tier. Used by: checker agent, synthesizer agent. |
| `THESIS_BALANCED_MODEL` | Yes | `gpt-4o-mini` | Model name for the balanced tier. |
| `THESIS_CHEAP_PROVIDER` | Yes | `deepseek` | Provider for the cheap tier. Used by: researcher agent. |
| `THESIS_CHEAP_MODEL` | Yes | `deepseek-chat` | Model name for the cheap tier. |

**How tiers map to agents:**

| Tier | Used by | Suggested model class |
|---|---|---|
| `quality` | HEAD planner, HEAD supervisor, critic | strongest / most capable |
| `balanced` | checker, synthesizer | mid-tier |
| `cheap` | researcher | cheapest / fastest |

**Minimal single-provider configuration (all tiers on DeepSeek):**

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

---

## Infrastructure

| Variable | Required | Default | Description |
|---|---|---|---|
| `REDIS_URL` | Yes | `redis://localhost:6379/0` | Redis connection URL. Used by the blackboard, session store, and search cache. In Docker Compose, set to `redis://redis:6379/0`. |
| `QDRANT_URL` | No | `""` | URL for a remote Qdrant instance (e.g. `http://qdrant:6333`). When empty, Qdrant runs embedded and writes to `./qdrant_data`. In Docker Compose, set to `http://qdrant:6333`. |
| `DATABASE_URL` | No | `""` | PostgreSQL DSN (e.g. `postgresql://postgres:postgres@localhost:5432/openworkers`). Required only if `SESSION_BACKEND=postgres`. When unset, sessions are stored in Redis. |

---

## Session Storage

| Variable | Required | Default | Description |
|---|---|---|---|
| `SESSION_BACKEND` | No | `""` | Selects the session persistence backend. Set to `postgres` to use PostgreSQL; leave empty to use Redis. When empty and `DATABASE_URL` is set, Postgres is selected automatically. |
| `SESSION_TTL_SECONDS` | No | `2592000` (30 days) | Expiry time for sessions stored in Redis. Has no effect when `SESSION_BACKEND=postgres`. |

---

## Search Cache

The search cache stores results from arXiv, Semantic Scholar, and CrossRef in Redis to avoid redundant network calls and upstream rate limits. It is a soft dependency: if Redis is unavailable the application falls back to live lookups.

| Variable | Required | Default | Description |
|---|---|---|---|
| `SEARCH_CACHE_ENABLED` | No | `true` | Set to `false` to disable search result caching entirely. |
| `SEARCH_CACHE_TTL_SECONDS` | No | `86400` (24 hours) | How long search results are cached in Redis. |

---

## Budget Guard

The budget guard enforces a per-session USD spending ceiling. When the ceiling is set, each LLM call is estimated before dispatch; calls that would push the session over the cap are skipped and the next provider in the fallback chain is tried. When `MAX_BUDGET_USD` is unset, the guard is disabled.

| Variable | Required | Default | Description |
|---|---|---|---|
| `MAX_BUDGET_USD` | No | `null` (disabled) | Per-session hard USD ceiling. Empty or unset disables the guard. Example: `0.50` caps each session at $0.50. |
| `BUDGET_OUTPUT_TOKEN_FLOOR` | No | `500` | Minimum assumed output tokens when estimating call cost. Higher values produce more conservative estimates. |

---

## Resilience

These settings control the retry and circuit-breaker behaviour applied to every LLM provider call. Retries handle transient errors (timeouts, 429s, 5xx); the circuit breaker trips after repeated failures to prevent cascading delays.

| Variable | Required | Default | Description |
|---|---|---|---|
| `RESILIENCE_RETRY_ATTEMPTS` | No | `3` | Maximum number of attempts per provider call (first try + retries). |
| `RESILIENCE_RETRY_BASE_SEC` | No | `0.5` | Exponential backoff base in seconds. The wait before retry N is approximately `base * 2^N` plus random jitter. |
| `RESILIENCE_RETRY_MAX_SEC` | No | `8.0` | Maximum backoff wait in seconds. Jittered waits are capped here. |
| `RESILIENCE_BREAKER_FAIL_MAX` | No | `5` | Number of consecutive failures before the circuit breaker opens for a provider. |
| `RESILIENCE_BREAKER_RESET_SEC` | No | `60` | Seconds the circuit breaker stays open before entering HALF_OPEN state and testing recovery. |

---

## Observability and Logging

| Variable | Required | Default | Description |
|---|---|---|---|
| `ENVIRONMENT` | No | `development` | Runtime environment name. Affects log format: `development` emits coloured console output; `production` emits newline-delimited JSON for log aggregators (Datadog, ELK, Cloud Logging). |
| `LOG_LEVEL` | No | `info` | Minimum log level. Accepts standard Python level names: `debug`, `info`, `warning`, `error`, `critical`. Applies to all loggers including third-party libraries (redis, httpx, qdrant). |

---

## Runtime Mode

| Variable | Required | Default | Description |
|---|---|---|---|
| `DRY_RUN` | No | `true` | When `true`, runs the full pipeline without making any LLM API calls. Useful for testing, demos, and CI. Set to `false` to enable real LLM calls. |

> **Note:** The default is `DRY_RUN=true` so the application does not make API calls unless explicitly enabled. Remember to set `DRY_RUN=false` in your `.env` for production use.

---

## Embedding Cache

FastEmbed model weights are cached by the FastEmbed library itself (in `~/.cache/fastembed/` or `~/.cache/huggingface/`). OpenWorkers adds a second cache layer for the embedding vectors produced from text strings, cutting per-query cost from ~20ms (CPU inference) to ~0.1ms (SQLite lookup).

| Variable | Required | Default | Description |
|---|---|---|---|
| `EMBEDDING_CACHE_DIR` | No | `""` | Directory for the diskcache SQLite database that stores computed embedding vectors. When empty, `core/embedding_cache.py` resolves the effective path to `~/.cache/openworkers/embeddings`. Survives container restarts when the directory is on a mounted volume. In Docker Compose, the `mcp` service mounts `fastembed_cache` at `/root/.cache/huggingface` for model weights; point this variable to a separate persistent path if you also want to cache vectors. |

---

## Per-Environment Overrides

Settings are read from environment variables first, with `.env` as a fallback. This means:

- **Development:** edit `.env` locally.
- **Docker Compose:** `.env` is loaded via `env_file:`, and the `environment:` block in `docker-compose.yml` overrides specific values (e.g., `REDIS_URL` and `QDRANT_URL` are set to the Compose service names).
- **Production/CI:** set variables directly in the host environment or deployment platform's secret manager. Variables set in the environment always take precedence over `.env`.

There are no `.env.development` or `.env.production` files — use the host environment or platform secrets for environment-specific overrides.
