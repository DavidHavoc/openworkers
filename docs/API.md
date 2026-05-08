<!-- generated-by: gsd-doc-writer -->
# API Reference

OpenWorkers exposes two interfaces: a **REST API** (FastAPI, `apps/api/main.py`) for long-running async tasks, and an **MCP server** (`apps/mcp_server/server.py`) for tool-calling clients that speak the Model Context Protocol over stdio.

---

## REST API

**Base URL:** <!-- VERIFY: production base URL -->  
**OpenAPI docs:** `/docs` (served by FastAPI automatically)  
**Version:** `0.2.0`

All request and response bodies are JSON. There is no authentication layer in the source — access control is handled at the infrastructure level. <!-- VERIFY: network-level auth / firewall details -->

### Endpoints

| Method | Path | Description | Status |
|--------|------|-------------|--------|
| `GET` | `/health` | Liveness check | 200 |
| `POST` | `/tasks/` | Submit a research task | 202 |
| `GET` | `/tasks/` | List all tasks | 200 |
| `GET` | `/tasks/{task_id}` | Get task status and result | 200 / 404 |
| `DELETE` | `/tasks/{task_id}` | Delete a task record | 204 / 404 |

---

### GET /health

Returns the server liveness status and the number of tasks currently held in memory.

**Response `200`**

```json
{
  "status": "ok",
  "tier": "api-gateway",
  "pending_tasks": 3
}
```

**Example**

```bash
curl http://localhost:8000/health
```

---

### POST /tasks/

Submit a research task. The task is queued immediately and executed asynchronously. Returns a `task_id` that can be polled via `GET /tasks/{task_id}`.

**Request body**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `query` | string | yes | — | The research question |
| `discipline` | string | no | `"general"` | Academic discipline (e.g. `"computer science"`) |
| `topic_summary` | string | no | `null` | Short summary of the topic; defaults to `query` if omitted |
| `existing_knowledge` | string | no | `null` | What the requester already knows |
| `what_they_need` | string | no | `null` | Specific help requested |
| `mode` | string | no | `"balanced"` | Quality/cost tradeoff: `"quality"`, `"balanced"`, or `"cheap"` |

**Response `202`**

```json
{
  "task_id": "a3f1e2c4-...",
  "status": "queued",
  "created_at": "2026-05-08T10:00:00+00:00"
}
```

**Example**

```bash
curl -X POST http://localhost:8000/tasks/ \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What are the effects of sleep deprivation on cognitive performance?",
    "discipline": "neuroscience",
    "mode": "balanced"
  }'
```

---

### GET /tasks/

List all tasks currently held in the in-memory store. Tasks are lost on server restart.

**Response `200`** — array of task summaries

```json
[
  {
    "task_id": "a3f1e2c4-...",
    "status": "complete",
    "created_at": "2026-05-08T10:00:00+00:00",
    "completed_at": "2026-05-08T10:01:45+00:00"
  }
]
```

**Task status values:** `queued` → `running` → `complete` | `failed`

**Example**

```bash
curl http://localhost:8000/tasks/
```

---

### GET /tasks/{task_id}

Retrieve the full status and result for a single task.

**Path parameter:** `task_id` — UUID returned by `POST /tasks/`

**Response `200`**

```json
{
  "task_id": "a3f1e2c4-...",
  "status": "complete",
  "created_at": "2026-05-08T10:00:00+00:00",
  "completed_at": "2026-05-08T10:01:45+00:00",
  "result": { ... },
  "error": null
}
```

When `status` is `"failed"`, `error` contains the exception message and `result` is `null`. When `status` is `"queued"` or `"running"`, both `result` and `completed_at` are `null`.

**Response `404`**

```json
{"detail": "Task 'a3f1e2c4-...' not found"}
```

**Example**

```bash
curl http://localhost:8000/tasks/a3f1e2c4-0000-0000-0000-000000000000
```

---

### DELETE /tasks/{task_id}

Remove a task record from the in-memory store. Returns no body on success.

**Path parameter:** `task_id` — UUID of the task to remove

**Response `204`** — no body

**Response `404`**

```json
{"detail": "Task 'a3f1e2c4-...' not found"}
```

**Example**

```bash
curl -X DELETE http://localhost:8000/tasks/a3f1e2c4-0000-0000-0000-000000000000
```

---

## MCP Server

The MCP server (`apps/mcp_server/`) implements the [Model Context Protocol](https://modelcontextprotocol.io) over **stdio** using newline-delimited JSON-RPC 2.0 (`protocolVersion: "2024-11-05"`). It is intended to be launched as a subprocess by an MCP host (e.g., Claude Desktop, an IDE extension).

**Server info:**
- `name`: `thesis-assistant`
- `version`: `0.1.0`
- `capabilities`: `tools`

All tool responses are returned inside the standard MCP `content` envelope:

```json
{
  "content": [
    { "type": "text", "text": "<JSON string>" }
  ]
}
```

The `text` value is always a JSON-serialised string. Errors are returned as a JSON object with a single `error` key inside that string rather than as a JSON-RPC error response.

### Tools

#### thesis_research

Run the full thesis assistant pipeline: literature search, classification, citation audit, and critique.

**Input schema**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `question` | string | yes | — | Research question |
| `summary` | string | no | — | Topic summary |
| `discipline` | string | no | `"general"` | Academic discipline |
| `knowledge` | string | no | `""` | What the caller already knows |
| `need` | string | no | `""` | What the caller needs help with |

**Return shape**

JSON-serialised `ThesisOrchestrator` session object (output of `session.model_dump()`). Structure mirrors the `ResearchContext` schema enriched with orchestrator results.

---

#### thesis_critique

Critique an idea, claim, or draft section. Returns structured feedback covering strengths, weaknesses, gaps, counterarguments, and suggestions.

**Input schema**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `text` | string | yes | — | Text to critique |
| `discipline` | string | no | `"general"` | Academic discipline |

**Return shape**

JSON-serialised critique object from `orch._critique_only()`.

---

#### thesis_verify_citation

Check whether a citation (DOI or paper title) is real by querying the CrossRef API. Returns verified metadata on success or `{"exists": false}` when the citation cannot be confirmed.

**Input schema**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `claim` | string | yes | DOI or paper identifier to verify |

**Return shape**

Verified metadata object, or `{"exists": false}`.

---

#### thesis_search_papers

Quick literature search against arXiv or Semantic Scholar. No LLM is involved. Returns papers with verified identifiers.

**Input schema**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `query` | string | yes | — | Search query |
| `source` | string | no | `"semantic_scholar"` | `"arxiv"` or `"semantic_scholar"` |
| `limit` | integer | no | `10` | Maximum number of papers to return |

**Return shape**

```json
{
  "papers": [ ... ],
  "query": "the original query string"
}
```

Each entry in `papers` is an object returned by the chosen literature source with verified paper IDs.

---

## Error Handling

### REST API

| Status | Meaning |
|--------|---------|
| `202` | Task accepted and queued |
| `204` | Task deleted successfully |
| `404` | Task ID not found in the in-memory store |

FastAPI also returns `422 Unprocessable Entity` for request body validation failures (missing required fields, wrong types).

### MCP Server

JSON-RPC errors use the following codes:

| Code | Meaning |
|------|---------|
| `-32700` | Parse error — request line was not valid JSON |
| `-32601` | Method not found — unsupported JSON-RPC method |
| `-32603` | Internal error — unexpected exception in the handler |

Unknown tool names return `{"error": "Unknown tool: <name>"}` inside the `content[0].text` field rather than a JSON-RPC error.
