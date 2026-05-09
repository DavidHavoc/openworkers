---
phase: openworkers-deep-review
reviewed: 2026-05-08T15:50:23Z
depth: deep
files_reviewed: 22
files_reviewed_list:
  - apps/api/main.py
  - apps/mcp_server/main.py
  - apps/worker/main.py
  - core/blackboard/engine.py
  - core/config.py
  - core/corpus/ingest.py
  - core/corpus/retrieve.py
  - core/embedding_cache.py
  - core/logging.py
  - core/memory/episodic.py
  - core/observability/metrics.py
  - core/orchestrator/thesis_flow.py
  - core/router/engine.py
  - core/sessions/store.py
  - providers/adapters.py
  - providers/budget.py
  - providers/resilience.py
  - providers/unified.py
  - pyproject.toml
  - tests/conftest.py
  - tests/test_budget.py
  - tools/cache.py
  - tools/mcp/rag.py
findings:
  critical: 5
  warning: 10
  info: 4
  total: 19
status: issues_found
---

# Code Review Report

**Reviewed:** 2026-05-08T15:50:23Z
**Depth:** deep
**Files Reviewed:** 22
**Status:** issues_found

## Summary

This is a research multi-agent system with a FastAPI gateway, a Qdrant-backed vector store, Redis-backed session/blackboard stores, and a multi-provider LLM routing layer. The codebase is architecturally coherent and shows deliberate design decisions (contextvars budget guard, circuit breakers, structured logging). However, deep cross-file analysis surfaces five blockers and ten warnings across concurrency correctness, data correctness, and security-adjacent quality issues.

The most severe issues are: (1) a hard `ValueError` raised on every memory-guidance blackboard write because `"memory_guidance"` is absent from the allowed-types allow-list; (2) a shared-state race condition where concurrent `execute()` calls on one `ThesisOrchestrator` instance mutate `self.blackboard` on a shared reference; (3) the API `DELETE /tasks/{task_id}` does not cancel the running asyncio task, so background work continues against a dropped dict entry. Additional blockers involve the OpenAI adapter issuing `asyncio.wait_for` on a coroutine that already carries a `timeout=` kwarg (redundant and can cause `TimeoutError` misattribution), and the `RedisSessionStore` calling blocking sync Redis operations directly in `async` methods without `asyncio.to_thread`.

---

## Critical Issues

### CR-01: `"memory_guidance"` is not a valid blackboard entry type — raises `ValueError` on every run

**File:** `core/orchestrator/thesis_flow.py:181-183`

**Issue:** `_run_memory()` calls `self._add_entry("memory_guidance", ...)`. The `Blackboard.add_entry` method (in `core/blackboard/engine.py:26-39`) validates against a hard-coded `allowed_types` set that does **not** include `"memory_guidance"`. This raises a `ValueError` on every non-dry-run execution. The error is silently swallowed by `_add_entry`'s bare `except Exception: pass` (line 76), so it is invisible in logs and tests. The effect is that memory guidance is **never written to the blackboard**, meaning the prompt compiler never receives it and the downstream agents operate without memory context.

**Fix:** Add `"memory_guidance"` to the `allowed_types` set in `core/blackboard/engine.py`:
```python
allowed_types = {
    "task",
    "evidence_ref",
    "route_decision",
    "agent_output",
    "status",
    "lit_search",
    "lit_map",
    "critique",
    "citation_audit",
    "corpus_benchmarks",
    "memory_guidance",   # ← add this
}
```

---

### CR-02: Concurrent `execute()` calls on a shared `ThesisOrchestrator` race on `self.blackboard`

**File:** `core/orchestrator/thesis_flow.py:106-108`

**Issue:** `_execute_inner` unconditionally overwrites `self.blackboard` with a new per-session `Blackboard` instance on every call:
```python
try:
    self.blackboard = Blackboard(session_id=session_id)
except Exception:
    self.blackboard = Blackboard(session_id=session_id)
```
If two `asyncio` tasks both call `execute()` on the same `ThesisOrchestrator` instance (which FastAPI enables because the orchestrator is created inside `_run_task` but Qdrant/Redis connections live on the shared instance), the second assignment replaces `self.blackboard` while the first `execute()` is still using it mid-pipeline. All subsequent `_add_entry`/`_get_entries` calls in the first coroutine operate on the wrong session's blackboard.

The comment at line 99 ("request-scoped state is threaded through the call chain") describes the fix for `rag_collection` but the blackboard is still assigned to `self`.

**Fix:** Promote `blackboard` to a local variable in `_execute_inner` instead of storing it on `self`, and pass it through the internal helpers:
```python
async def _execute_inner(self, research_context: ResearchContext) -> ResearchSession:
    ...
    blackboard = Blackboard(session_id=session_id)

    def _add_entry(entry_type, content):
        try:
            return blackboard.add_entry(entry_type, content)
        except Exception:
            pass
        return None

    def _get_entries():
        try:
            return blackboard.get_all_entries()
        except Exception:
            return []
    ...
```

---

### CR-03: `DELETE /tasks/{task_id}` removes the dict entry without cancelling the in-flight asyncio task

**File:** `apps/api/main.py:141-145`

**Issue:** `asyncio.create_task(_run_task(task_id, request))` at line 109 spawns a background task but the `Task` object is never stored. `delete_task` removes `_tasks[task_id]` from the dict but the background coroutine at `_run_task` continues running. When it eventually completes or errors it writes `_tasks[task_id]["status"] = ...` (lines 83, 88), causing a `KeyError` since the key was deleted. The exception propagates unhandled inside the task, and the task's exception is never awaited, producing an unhandled asyncio task exception.

**Fix:** Store the `asyncio.Task` alongside the task dict entry and cancel it on deletion:
```python
_tasks: Dict[str, Dict[str, Any]] = {}
_task_handles: Dict[str, asyncio.Task] = {}

# in submit_task:
handle = asyncio.create_task(_run_task(task_id, request))
_task_handles[task_id] = handle

# in delete_task:
handle = _task_handles.pop(task_id, None)
if handle and not handle.done():
    handle.cancel()
del _tasks[task_id]
```

Also add a guard in `_run_task` to check `if task_id not in _tasks: return` after each `await` point.

---

### CR-04: `RedisSessionStore` calls blocking sync Redis I/O directly in `async` methods

**File:** `core/sessions/store.py:46-98`

**Issue:** `RedisSessionStore.save`, `load`, `list_sessions`, `delete`, and `count` are declared `async` but every Redis call inside them is synchronous (e.g., `self.redis.get(...)`, `self.redis.zrevrange(...)`, `pipe.execute()` at line 54). Under FastAPI's async event loop these blocking calls park the event loop thread for the duration of each Redis I/O (typically 0.5–5 ms each). Under concurrent request load this compounds into event-loop stall, degrading all in-flight requests. More critically, a slow or unresponsive Redis server can block the entire process.

**Fix:** Either use `redis.asyncio` (the async Redis client) or wrap each call in `asyncio.to_thread`:
```python
import redis.asyncio as aioredis

class RedisSessionStore(BaseSessionStore):
    def __init__(self, redis_url: Optional[str] = None) -> None:
        url = redis_url or get_settings().redis_url
        self.redis = aioredis.from_url(url, decode_responses=True)

    async def save(self, session: ResearchSession) -> None:
        session_key = f"{_SESSION_KEY_PREFIX}{session.session_id}"
        data = session.model_dump_json()
        async with self.redis.pipeline() as pipe:
            await pipe.setex(session_key, get_settings().session_ttl_seconds, data)
            await pipe.zadd(_INDEX_KEY, {session.session_id: time.time()})
            await pipe.execute()
```

---

### CR-05: `_generate_openai` applies both an SDK-level `timeout=120` kwarg and `asyncio.wait_for(..., timeout=120)` — duplicate timeout with misattributed error

**File:** `providers/adapters.py:143-165`

**Issue:** The `kwargs` dict passed to `openai_client.chat.completions.create` includes `"timeout": 120` (line 146). The returned coroutine is then wrapped in `asyncio.wait_for(..., timeout=120)` (lines 162–165). If the SDK-level timeout fires first it raises an `openai.APITimeoutError`; if `asyncio.wait_for` fires first it raises `asyncio.TimeoutError`. The `except asyncio.TimeoutError` handler at line 81 only catches the asyncio variant, meaning the SDK-level `APITimeoutError` propagates directly to `except Exception` at line 84 with a less informative re-raise. More seriously, the double wrapping means the effective timeout is not 120 s but whichever fires first — if there is any event-loop stall, `asyncio.wait_for` may fire while the SDK is still waiting, leaving the underlying HTTP connection open.

**Fix:** Remove `"timeout"` from `kwargs` and rely solely on `asyncio.wait_for`, or remove `asyncio.wait_for` and let the SDK handle the timeout:
```python
kwargs: Dict[str, Any] = {
    "model": model_name,
    "messages": messages,
    # remove "timeout" here — handled by asyncio.wait_for below
}
...
response = await asyncio.wait_for(
    self.openai_client.chat.completions.create(**kwargs),
    timeout=120,
)
```

---

## Warnings

### WR-01: `_add_entry` silently swallows all exceptions, masking `ValueError` and Redis errors

**File:** `core/orchestrator/thesis_flow.py:73-78`

**Issue:** The bare `except Exception: pass` at line 76 hides every error from `Blackboard.add_entry`, including the `ValueError` triggered by CR-01. This turns a programming error into silent data loss — the blackboard entry is never written and the caller has no way to know. This pattern is repeated in `_get_entries` (line 83).

**Fix:** At minimum log the error at `WARNING` level so it appears in logs:
```python
def _add_entry(self, entry_type: str, content: Dict[str, Any]) -> Optional[BlackboardEntry]:
    try:
        return self.blackboard.add_entry(entry_type, content)
    except Exception as exc:
        logger.warning("Blackboard write failed (entry_type=%s): %s", entry_type, exc)
    return None
```

---

### WR-02: Identical try/except branches in `_execute_inner` blackboard initialisation — dead code

**File:** `core/orchestrator/thesis_flow.py:105-108`

**Issue:**
```python
try:
    self.blackboard = Blackboard(session_id=session_id)
except Exception:
    self.blackboard = Blackboard(session_id=session_id)
```
Both branches perform the exact same operation. The `except` path cannot recover from a `Blackboard` construction failure because it makes the identical call. This is either dead code or a forgotten fallback (e.g., intended to create an in-memory fallback). If `Blackboard.__init__` raises (e.g., Redis unreachable), the exception in the `except` block propagates unhandled, bypassing the intended silent-fallback behaviour.

**Fix:** Either implement a real fallback (e.g., an in-process no-op blackboard) or remove the try/except and let the exception propagate with a meaningful error:
```python
self.blackboard = Blackboard(session_id=session_id)
```

---

### WR-03: `core/embedding_cache.py` reads `EMBEDDING_CACHE_DIR` directly from `os.environ`, bypassing the `Settings` singleton

**File:** `core/embedding_cache.py:47`

**Issue:** `_get_cache()` reads `os.environ.get("EMBEDDING_CACHE_DIR", "")` instead of `get_settings().embedding_cache_dir`. This means `monkeypatch.setenv("EMBEDDING_CACHE_DIR", ...)` in tests does not go through `reset_settings()` and may be invisible to the singleton if it was already built. The `conftest.py` fixture does not call `reset_embedding_cache()`, so tests that rely on a custom cache dir will fail silently. This also means the documented override mechanism (`Settings.embedding_cache_dir`) is non-functional.

**Fix:**
```python
from core.config import get_settings

def _get_cache() -> Any | None:
    global _disk
    if _disk is not None:
        return _disk
    try:
        import diskcache
    except ImportError:
        logger.debug("diskcache not installed; embedding cache disabled")
        return None

    cache_dir = get_settings().embedding_cache_dir or _DEFAULT_CACHE_DIR
    _disk = diskcache.Cache(cache_dir)
    return _disk
```

Also add `reset_embedding_cache()` to the `_reset_singletons` fixture in `tests/conftest.py`.

---

### WR-04: `Blackboard.get_entries_by_type` and `get_all_entries` use `redis.keys()` — O(N) blocking scan on the entire Redis keyspace

**File:** `core/blackboard/engine.py:54-77`

**Issue:** `redis.keys(pattern)` is a blocking O(N) command that scans every key in the database. Under a shared Redis instance with many sessions or other tenants this can block Redis for tens of milliseconds, stalling all other clients. The Redis documentation explicitly warns against `KEYS` in production. The same issue exists in `RedisSessionStore.clear_all` (line 101) and `tools/cache.py` uses `scan_iter` (correct) but blackboard does not.

**Fix:** Replace `self.redis.keys(...)` with `self.redis.scan_iter(...)`:
```python
def get_entries_by_type(self, entry_type: str) -> List[BlackboardEntry]:
    entries = []
    for k in self.redis.scan_iter(f"{self.prefix}*"):
        data = self.redis.get(k)
        if data:
            entry = BlackboardEntry.model_validate_json(data)
            if entry.entry_type == entry_type:
                entries.append(entry)
    return sorted(entries, key=lambda x: x.timestamp)
```

---

### WR-05: `_estimate_cost` in `UnifiedLLM` measures only output length — dramatically underestimates cost for long prompts

**File:** `providers/unified.py:349-352`

**Issue:**
```python
def _estimate_cost(self, text: str, provider: str) -> float:
    tokens = len(text) / 3.5
    rate = COST_PER_1K_TOKENS.get(provider, 0.005)
    return (tokens / 1000) * rate
```
`text` here is the **response** string only. Input token cost is entirely omitted. For the thesis pipeline the system prompt + blackboard entries can be several thousand tokens (often larger than the response), so the recorded cost (`cost_estimate_usd` in `LLMResponse`) and the amount passed to `guard.record_actual(cost)` (line 262) are systematically under-reported. The `BudgetGuard.check()` pre-estimate does include input (via `guard.estimate(prompt, system_prompt, provider)`), but the post-call accounting in `_session_spend` and the guard's `spent_usd` both miss it. This can allow cumulative spend to exceed `MAX_BUDGET_USD` because `guard.record_actual` is fed a fraction of the real cost.

**Fix:**
```python
def _estimate_cost(self, response_text: str, provider: str,
                   prompt: str = "", system_prompt: str = "") -> float:
    output_tokens = len(response_text) / 3.5
    input_tokens = (len(prompt) + len(system_prompt)) / 3.5
    rate = COST_PER_1K_TOKENS.get(provider, 0.005)
    return ((input_tokens + output_tokens) / 1000) * rate
```
Pass `prompt` and `system_prompt` through to `_estimate_cost` at the call site (line 257).

---

### WR-06: `router.route_thesis_task` sets `activate_synthesizer = True` then may immediately set it `False` — contradictory logic

**File:** `core/router/engine.py:155-167`

**Issue:**
```python
route.activate_synthesizer = not budget_tight  # True when budget is fine
route.activate_critic = True
route.activate_head_supervisor = True
reasons.append("full pipeline: all agents")

if budget_tight:
    reasons.append("budget tight: synthesizer skipped")
    if route.activate_synthesizer:          # always False here when budget_tight
        route.activate_synthesizer = False  # dead branch
```
When `budget_tight` is `True`, `activate_synthesizer` is already set to `False` on line 159 (`not budget_tight`). The `if route.activate_synthesizer:` guard at line 166 is therefore always `False` when the body at line 167 would need to run, making it dead code. This is a logic inversion: the intent is clear, but a future refactor could break the invariant silently.

**Fix:** Simplify to remove the redundant guard:
```python
if budget_tight:
    route.activate_synthesizer = False
    reasons.append("budget tight: synthesizer skipped")
```

---

### WR-07: `apps/api/main.py` — `_tasks` grows indefinitely; no eviction or size cap

**File:** `apps/api/main.py:29`

**Issue:** The in-process `_tasks: Dict[str, Dict[str, Any]]` dictionary is never pruned. Every submitted task — including its full `session.model_dump()` result which may be tens of kilobytes — accumulates in memory for the lifetime of the process. Under sustained load (e.g., 100 tasks/hour × 8-hour uptime = 800 entries × ~50 KB each = ~40 MB) this will grow to exhaust available memory. There is also no `maxsize` guard or TTL-based eviction.

**Fix:** Use a bounded LRU eviction (e.g., `collections.OrderedDict` with a maximum size), or store task results in the existing `RedisSessionStore` instead of an in-process dict:
```python
from collections import OrderedDict
_MAX_TASKS = 1000
_tasks: OrderedDict[str, Dict[str, Any]] = OrderedDict()

# In submit_task, after inserting:
while len(_tasks) > _MAX_TASKS:
    _tasks.popitem(last=False)
```

---

### WR-08: `datetime.utcnow()` used in multiple places — deprecated in Python 3.12 and produces naive datetimes

**Files:** `core/blackboard/engine.py:46`, `core/orchestrator/thesis_flow.py:395`, `core/orchestrator/thesis_flow.py:406`, `core/orchestrator/thesis_flow.py:650`

**Issue:** `datetime.utcnow()` is deprecated since Python 3.12 (`DeprecationWarning` in 3.12, planned for removal in 3.14). It returns a naive `datetime` object without timezone info. The code appends `"Z"` manually to produce an ISO 8601 UTC timestamp, but the string is produced from a naive object. This is a semantic mismatch: consumers that parse the string back to a timezone-aware datetime with `datetime.fromisoformat(...)` (Python 3.11+) will succeed, but those using `datetime.strptime` will get a naive object silently. `core/sessions/store.py:75` uses `datetime.utcfromtimestamp()` which has the same issue.

**Fix:** Replace with `datetime.now(timezone.utc)`:
```python
from datetime import datetime, timezone
# Replace:
timestamp=datetime.utcnow().isoformat() + "Z",
# With:
timestamp=datetime.now(timezone.utc).isoformat(),
```

---

### WR-09: `_is_sentence_terminator` is defined but never called — dead code

**File:** `tools/mcp/rag.py:87-98`

**Issue:** `_is_sentence_terminator(words_so_far: list[str])` is defined but never invoked anywhere in the file or across the codebase. `_split_sentences` uses `_ends_with_abbrev` (line 113) instead. The presence of `_is_sentence_terminator` alongside the similar-but-unused signature `(words_so_far: list[str])` (vs. `_ends_with_abbrev(sentence: str)`) suggests it was an earlier iteration that was not removed.

**Fix:** Remove the dead function:
```python
# Delete lines 87-98 (def _is_sentence_terminator)
```

---

### WR-10: `core/corpus/ingest.py:233-237` uses `print()` to stderr instead of the logger

**File:** `core/corpus/ingest.py:233-237`

**Issue:**
```python
except Exception as e:
    import sys
    print(f"[CorpusIngest] add failed: {e}", file=sys.stderr)
    raise
```
This bypasses the structlog processor chain configured in `core/logging.py`. In production the message will not be emitted as JSON, will not carry session/trace context, and will not be capturable by log aggregators that consume stdout/stderr in structured form. The `import sys` inside the `except` block is also a deferred import in a hot path.

**Fix:**
```python
import logging
logger = logging.getLogger(__name__)

# In _ingest_raw:
except Exception as e:
    logger.error("CorpusIngest.add failed: %s", e, exc_info=True)
    raise
```

---

## Info

### IN-01: `apps/worker/main.py` is an infinite sleep loop — not a real worker

**File:** `apps/worker/main.py:13-15`

**Issue:** The worker runs `while True: await asyncio.sleep(10)` with no connection to a task queue, no job consumption, and no signal handling. The comment acknowledges this ("In a real app, this might connect to a Redis queue"). This is not directly a bug in the current system, but if deployed it will consume a process slot and emit heartbeat logs while performing no work. If the application is scaled expecting workers to handle tasks, all tasks will silently queue indefinitely.

**Fix:** Either implement the task consumer (e.g., connect to a Redis list via `brpop`) or remove the worker process entrypoint until it is needed.

---

### IN-02: `_generate_placeholder_json` is duplicated between `providers/adapters.py` and `providers/unified.py`

**Files:** `providers/adapters.py:170-186`, `providers/unified.py:47-63`

**Issue:** Both files contain an identical `_generate_placeholder_json(schema)` function. The copies are byte-for-byte identical in logic. Any bug fix or enhancement must be applied twice; they will inevitably diverge.

**Fix:** Move the function to a shared internal module (e.g., `providers/_utils.py`) and import it in both files.

---

### IN-03: `providers/adapters.py` imports `_build_placeholder_*` private functions from `providers/thesis_agents`

**File:** `core/orchestrator/thesis_flow.py:39-44`

**Issue:** `thesis_flow.py` imports five `_build_placeholder_*` functions using their private-by-convention `_` prefix from `providers.thesis_agents`. Importing private symbols across modules creates a tight coupling that prevents the `providers.thesis_agents` module from refactoring its internals without breaking `thesis_flow`. This also complicates testing since `thesis_flow` depends on implementation details.

**Fix:** Promote these functions to public API or move them to a dedicated `providers/placeholders.py` module that both files can import openly.

---

### IN-04: `_coerce_budget` validator treats `0.0` as "unset" due to falsy check

**File:** `core/config.py:81-89`

**Issue:**
```python
@field_validator("max_budget_usd", mode="before")
@classmethod
def _coerce_budget(cls, v: Any) -> Optional[float]:
    if not v:
        return None
```
`if not v:` evaluates to `True` for `v = 0` or `v = 0.0`. A user who sets `MAX_BUDGET_USD=0` intending a zero budget (block all LLM calls) would silently get `None` (guard disabled, unlimited spend). The `BudgetGuard.enabled` property returns `False` when `max_usd is None`, so the guard would be off.

**Fix:** Check for empty string explicitly:
```python
if v is None or v == "":
    return None
try:
    return float(v)
except (ValueError, TypeError):
    return None
```

---

_Reviewed: 2026-05-08T15:50:23Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
