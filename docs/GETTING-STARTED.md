<!-- generated-by: gsd-doc-writer -->
# Getting Started

This guide walks you from a fresh checkout to a working `thesis research` command in four steps. It covers local installation only — for Docker see the [Docker section in the README](../README.md#docker).

---

## Prerequisites

| Requirement | Minimum version | Notes |
|---|---|---|
| Python | `>= 3.9` | 3.12 recommended; the test matrix runs both 3.9 and 3.12 |
| Redis | 7.x | Required at runtime for the blackboard and session store |
| Git | any recent | To clone the repo |

Redis must be reachable at `redis://localhost:6379/0` before you start the CLI or API. The quickest way to get Redis running locally:

```bash
# Docker (no local Redis install needed)
docker run -d -p 6379:6379 redis:7-alpine
```

Or install via your OS package manager (`brew install redis`, `apt install redis-server`, etc.) and start with `redis-server`.

**Qdrant** runs embedded by default — no separate process needed. Leave `QDRANT_URL` unset in your `.env` and Qdrant writes its data to `./qdrant_data` automatically.

---

## Installation

```bash
git clone https://github.com/DavidHavoc/openworkers.git
cd openworkers
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

This installs the `thesis` CLI entry point and all runtime and dev dependencies. Confirm the install worked:

```bash
thesis --help
```

You should see the list of subcommands (`research`, `critique`, `verify`, `papers`, etc.).

---

## Configuration

Copy the example env file:

```bash
cp .env.example .env
```

### Option A — No API keys (dry run)

`DRY_RUN=true` runs the full pipeline without making any LLM API calls. The agents execute, the blackboard fills, and you get a structurally correct `ResearchSession` output with placeholder content. Useful for verifying your install before spending tokens.

Your `.env` needs only:

```env
REDIS_URL=redis://localhost:6379/0
DRY_RUN=true
```

The LLM provider and model variables can be left blank or commented out in dry-run mode.

### Option B — Single provider (real calls)

Pick one provider, add its API key, set all three tiers to that provider, and set `DRY_RUN=false`. Example using DeepSeek (lowest cost):

```env
REDIS_URL=redis://localhost:6379/0

DEEPSEEK_API_KEY=sk-...
THESIS_QUALITY_PROVIDER=deepseek
THESIS_QUALITY_MODEL=deepseek-chat
THESIS_BALANCED_PROVIDER=deepseek
THESIS_BALANCED_MODEL=deepseek-chat
THESIS_CHEAP_PROVIDER=deepseek
THESIS_CHEAP_MODEL=deepseek-chat

DRY_RUN=false
```

The three tiers (`quality`, `balanced`, `cheap`) can point to different providers and models once you have more than one API key. See [docs/CONFIGURATION.md](CONFIGURATION.md) for the full variable reference.

> **Note:** The default value of `DRY_RUN` in `.env.example` is `false`. The CLI sets it to `true` internally as a fallback only if you have not set it at all. Set it explicitly in your `.env` to avoid ambiguity.

---

## First run

Make sure Redis is running, then:

```bash
thesis research "Does retrieval-augmented generation improve factuality in large language models?" --discipline computer_science
```

### What to expect

With `DRY_RUN=true` the pipeline completes in a few seconds. You will see structured text output with the following shape:

```
Session: <session-id>
Question: Does retrieval-augmented generation improve factuality...
Discipline: computer_science
Status: complete

Literature Map
  Supporting: ...
  Challenging: ...
  Adjacent: ...

Citation Audit
  ...

Synthesis Report
  ...

Critique
  Strengths: ...
  Weaknesses: ...
  Gaps: ...
  Suggestions: ...
```

With a real provider (`DRY_RUN=false`) the same structure is returned with content sourced from arXiv, Semantic Scholar, CrossRef, and the configured LLM.

To get machine-readable output:

```bash
thesis research "Does RAG improve factuality?" --discipline computer_science --format json --output result.json
```

---

## Common setup issues

**Redis connection refused**

```
redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379
```

Redis is not running. Start it with `docker run -d -p 6379:6379 redis:7-alpine` or your local Redis service.

**`thesis: command not found` after install**

The `.venv` is not activated. Run `source .venv/bin/activate` and try again. If you used a different venv tool (`pyenv`, `conda`, etc.) make sure the correct environment is active.

**`pydantic_settings.env_settings.SettingsError` or missing provider key**

`DRY_RUN=false` is set but the provider env vars (`THESIS_QUALITY_PROVIDER`, etc.) are missing or point to a provider with no API key. Either set `DRY_RUN=true` or add the provider variables and API key as shown in Option B above.

**Qdrant `qdrant_data/` permission error**

The embedded Qdrant instance writes to `./qdrant_data` in the project root. Make sure the directory (or its parent) is writable by the current user. Override the location with `QDRANT_URL` pointing to a remote instance if needed.

---

## Next steps

- [docs/CONFIGURATION.md](CONFIGURATION.md) — full environment variable reference, budget guard, resilience tuning
- [docs/architecture.md](architecture.md) — pipeline stages, routing tiers, persistence backends
- [README.md](../README.md) — Docker Compose stack, MCP server setup, FastAPI endpoints
- [DEVELOPMENT.md](DEVELOPMENT.md) — build commands, linting, test suite
