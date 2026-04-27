# OpenWorkers

A research-focused hierarchical multi-agent system MVP.

## Overview

This project implements an agentic backend where:
- A trusted **HEAD** agent routes research tasks.
- Optional **Middle-tier** agents clean, deduplicate, cluster, and summarize.
- Optional **Worker** agents perform bounded, low-risk, concurrent tasks.
- **MCP Tools** provide external integration (Web, Knowledge, Data Lookup).
- An **Episodic Routing Memory** system stores past execution patterns to heavily bias future routing based on cost, quality, and latency.

The system uses deterministic routing heuristics initially (v1), to establish clear trust boundaries and predictable performance before introducing ML or RL-based optimizers.

## Tech Stack
- **Python 3.12**
- **FastAPI**: API delivery and synchronization endpoints.
- **Pydantic**: Type-safety and data contracts.
- **Postgres**: Durable episodic knowledge and user state.
- **Redis**: Fast broker and blackboard sharing.
- **Docker Compose** & **pytest**.

## Architecture & Data
See [docs/architecture.md](docs/architecture.md) for a detailed breakdown of the tiers and components.
See [docs/examples.md](docs/examples.md) for structure samples like Memory Episodes and routing outputs.

## local development runbook

### 1. Requirements
Ensure Python 3.12 is installed, running inside a virtual environment.

```bash
python3 -m venv .venv
source .venv/bin/activate
make install
```

### 2. Provider configuration
Copy `.env.example` to `.env`. Turn `DRY_RUN=true` to execute offline simulated behavior. Turn to `DRY_RUN=false` once you append explicit LLM Keys.

### 3. Evoking the Harness
We established an offline evaluation harness reproducing three different bounding criteria tasks natively. Run:

```bash
python core/evals/harness.py
```

### 4. Running unit & integration bounds

```bash
pytest tests/ -v
```

## troubleshooting

- **`ImportError: cannot import name ...`**: Ensure your execution points load `.venv/bin/activate`. If using standard Python path structures, ensure you are running `pytest` natively from the repo root to recognize local modules like `providers.adapters`.
- **`Security Violation: Tier 'public' not allowed`**: This is an intended strict override. Ensure you are passing correct context permissions directly during system mapping to limit `KnowledgeRetrievalTool` access securely.
- **No logs mapping observed execution traces**: Ensure you are observing terminal output directly via `./venv/bin/python`, as the `StructuredLogger` leverages default STDOUT streams dynamically bound upon first orchestrator fetch.
