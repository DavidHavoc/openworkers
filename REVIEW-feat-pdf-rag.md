---
phase: feat-pdf-rag
reviewed: 2026-05-08T00:00:00Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - tools/mcp/rag.py
  - tools/mcp/engine.py
  - core/schemas.py
  - core/orchestrator/thesis_flow.py
  - apps/cli/main.py
  - tests/test_rag.py
findings:
  critical: 1
  warning: 7
  info: 6
  total: 14
status: issues_found
---

# feat/pdf-rag: Code Review Report

**Reviewed:** 2026-05-08
**Depth:** standard
**Files Reviewed:** 6
**Status:** issues_found

## Summary

The branch adds a clean, additive RAG-over-user-PDFs feature: a chunker + indexer
in `tools/mcp/rag.py`, a `rag_search` MCP tool that mirrors the academic-tool
output shape, a `thesis ingest add|list|delete` CLI subcommand, and an optional
`rag_collection` field on `ResearchContext`. Reuses the existing FastEmbed/Qdrant
stack with the `BAAI/bge-small-en-v1.5` model already used by
`core.memory.episodic` and `core.corpus.*`, so no second embedding stack is
introduced. Backward compatibility is preserved: `rag_collection` defaults to
`None` and is read defensively via `getattr` in the CLI.

That said, several real defects sit alongside the new feature:

- **One BLOCKER**: the orchestrator stashes the active collection in
  `self._active_rag_collection` instance state, which leaks across concurrent
  requests when the orchestrator is reused (e.g. by `apps/api/main.py`).
- **Several WARNINGs**: the BOM-strip uses a substring instead of a codepoint set
  (still works but accidentally), the `delete` subcommand path-traverses to the
  wrong collection because of the same sanitization quirk that the test
  `test_collection_for_sanitizes_unsafe_chars` documents, the chunker can let a
  single oversized sentence exceed `max_words` without warning, and several
  tests are tautological or so loose that real regressions slip through.
- **INFO**: a few minor smells (duplicated string literals, no extension allowlist,
  a tail-overlap edge case with single-word sentences, a swallowed-exception hook
  in the orchestrator's RAG side-channel).

Performance issues (model-load cost on every CLI invocation, double-indexing on
re-ingest because deterministic IDs only stabilise when chunk text is byte-identical)
are out of scope for v1 and not flagged.

## Critical Issues

### CR-01: `_active_rag_collection` leaks across concurrent orchestrator calls

**File:** `core/orchestrator/thesis_flow.py:88`
**Issue:**
`ThesisOrchestrator.execute` stores the request-scoped collection on the
orchestrator instance:

```python
self._active_rag_collection = research_context.rag_collection or None
```

`_search_rag_for_plan` later reads it with
`getattr(self, "_active_rag_collection", None)` (line 454). If two `execute()`
calls overlap on the same orchestrator instance — which is exactly what happens
under `apps/api/main.py` where the orchestrator is constructed once at startup
and reused — request B can overwrite the attribute mid-flight, causing request
A's literature search to query request B's collection (or vice-versa, leak
B's collection into A).

The CLI is safe today because `_create_orchestrator()` builds a fresh instance
per invocation, but the orchestrator was clearly designed to be reused (see
the recent `c056623` commit "pre-init orchestrator at startup"). This is a
data-leak between users, not just a functional bug.

**Fix:** Don't smuggle it through `self`. Pass the collection explicitly down
the call chain:

```python
# in execute()
rag_collection = research_context.rag_collection or None
# ...
raw_papers = await self._search_literature_from_plan(research_plan, rag_collection)

async def _search_literature_from_plan(
    self, plan: Optional[ResearchPlan], rag_collection: Optional[str] = None
) -> List[Dict[str, Any]]:
    ...
    rag_papers = await self._search_rag_for_plan(plan, rag_collection)
    ...

async def _search_rag_for_plan(
    self, plan: Optional[ResearchPlan], collection: Optional[str]
) -> List[Dict[str, Any]]:
    if not collection or self.tool_registry is None:
        return []
    ...
```

This also kills the `getattr(self, "_active_rag_collection", None)` defensive
read, which was a tell that the author knew the attribute might not be set.

## Warnings

### WR-01: Collection-name sanitiser collapses distinct paths into the same collection

**File:** `tools/mcp/rag.py:39-41`
**Issue:**
```python
def _collection_for(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", name).strip("_") or "default"
    return f"{COLLECTION_PREFIX}{safe}"
```
The test `test_collection_for_sanitizes_unsafe_chars` cheerfully asserts
`_collection_for("../etc/passwd") == "rag_etc_passwd"` — i.e. a user who
intends to delete the collection literally named `etc/passwd` and a user
who pastes `../etc/passwd` end up writing to (and deleting) the same
collection. Same problem for any of `etc_passwd`, `..etc..passwd`,
`/etc/passwd/`, `etc!passwd`, etc. — they all collapse to `rag_etc_passwd`.

This is benign for ingestion (just lossy naming) but the CLI exposes
`thesis ingest delete <collection>`, which means a typo or a copy-pasted
path can permanently destroy a different user's collection in shared
deployments. It also means `list_collections()` returns the *sanitised*
name, so the round-trip through `delete` will silently target a different
collection than the one the user thought they were naming.

**Fix:** Either (a) reject names containing non-`[a-zA-Z0-9_-]` characters
outright — the CLI is the only ingress point, so it's cheap to validate
on input; or (b) keep sanitisation but disambiguate by hashing the
original name into the suffix:

```python
def _collection_for(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", name).strip("_")
    if not safe:
        return f"{COLLECTION_PREFIX}default"
    if safe != name:
        suffix = hashlib.md5(name.encode()).hexdigest()[:6]
        return f"{COLLECTION_PREFIX}{safe}_{suffix}"
    return f"{COLLECTION_PREFIX}{safe}"
```

At minimum, document the lossy mapping in the `--collection` CLI help so
users know `../etc/passwd` and `etc/passwd` are the same bucket.

### WR-02: Single oversized sentence can produce a chunk >> `max_words`

**File:** `tools/mcp/rag.py:75-90`
**Issue:**
The flush condition is `if buf_words + len(words) > max_words and buf:`. If
a single sentence already exceeds `max_words`, the `and buf` short-circuit
keeps appending into an empty buffer until the sentence fits — producing a
chunk that can be arbitrarily larger than `max_words`. This breaks the
contract advertised by the function's docstring ("packs sentences into
chunks up to `max_words` long") and breaks the implicit assumption that
embeddings see roughly uniformly sized inputs.

PDFs from real LaTeX exports routinely produce a single "sentence" hundreds
of words long when bibliography lines or equation captions don't end in
`.!?`. The test `test_chunk_text_splits_at_max_words_with_sentence_boundary`
nominally guards against this with
`assert words <= 100 + 20 + 11`, but the cap is a fudge factor specific
to the synthetic input — it doesn't catch the unbounded case.

**Fix:** When `len(words) > max_words`, hard-split the sentence into
word-windows of `max_words` (with overlap) before/instead of appending.

```python
if len(words) > max_words:
    # flush current buf first
    if buf:
        chunks.append(" ".join(buf))
        buf, buf_words = [], 0
    step = max_words - overlap_words
    for i in range(0, len(words), step):
        chunks.append(" ".join(words[i : i + max_words]))
    continue
```

### WR-03: BOM strip relies on `lstrip` accepting a multi-byte char as a "set of chars"

**File:** `tools/mcp/rag.py:121`
**Issue:**
```python
return f.read().lstrip("﻿")
```
The argument is a *single* codepoint (U+FEFF), so this happens to do the
right thing — but `str.lstrip(chars)` treats `chars` as a *set* of
characters to strip. Anyone reading this thinks it's stripping a BOM
substring, and the next person who edits it (e.g. to also strip a
shebang or another magic prefix) will silently break it. Also, files
opened with `encoding="utf-8"` do not strip the BOM — the U+FEFF is
preserved as-is, which is what makes this work, but that's a fragile
implicit dependency.

**Fix:** Use `utf-8-sig`, which strips the BOM during decode and leaves
the rest of the contract identical:

```python
with open(path, encoding="utf-8-sig", errors="ignore") as f:
    return f.read()
```

Then drop the `.lstrip(...)`. This also removes the dependency on the BOM
character literal sitting in the source file (which is itself a hazard —
many editors silently strip leading BOMs from `.py` files on save).

### WR-04: `chunk_text` overlap loop loses words when sentences are extremely short

**File:** `tools/mcp/rag.py:78-85`
**Issue:**
```python
for prev in reversed(buf):
    tail_words = prev.split() + tail_words
    if len(tail_words) >= overlap_words:
        break
tail = " ".join(tail_words[-overlap_words:])
```
If `buf` has fewer total words than `overlap_words` (e.g. the very first
chunk overflowed on a single very long sentence — see WR-02), the loop
exits without breaking and `tail_words` has all available words.
`tail_words[-overlap_words:]` then returns *all* of them, which is fine.

But there's a more subtle issue: when `buf` after overlap-rebuild becomes
`[tail]` (a single string of `overlap_words` words), and the next sentence
is appended, the next overlap rebuild iterates `reversed(buf)` starting
with that next sentence. If that sentence is short, the loop walks back
into the `tail` string — which itself contains words that came from the
chunk *before* the previous one. So overlap can cascade: chunk N+2 can
contain words that originated in chunk N. This is "more overlap than
asked for", not a correctness bug, but it bloats storage and weakens
retrieval signals at chunk boundaries.

**Fix:** Track the overlap as a flat list of words separately from the
sentence buffer, so it never re-feeds itself:

```python
chunks: List[str] = []
buf: List[str] = []          # current chunk's sentences
buf_words = 0
carry: List[str] = []        # words to prepend to next chunk

for sentence in sentences:
    words = sentence.split()
    if not words:
        continue
    if buf_words + len(words) > max_words and buf:
        chunks.append(" ".join(carry + buf) if carry else " ".join(buf))
        flat = " ".join(buf).split()
        carry = flat[-overlap_words:] if overlap_words else []
        buf, buf_words = [], 0
    buf.append(sentence)
    buf_words += len(words)

if buf:
    chunks.append(" ".join(carry + buf) if carry else " ".join(buf))
```

### WR-05: `extract_text` accepts arbitrary file extensions, returns garbage for non-text inputs

**File:** `tools/mcp/rag.py:97-121`
**Issue:**
The function only special-cases `.pdf`. For everything else — `.exe`,
`.docx`, `.zip`, `.png` — it falls through to
`open(path, encoding="utf-8", errors="ignore").read()` and returns
whatever `errors="ignore"` salvages. The result then gets embedded and
stored, polluting the user's collection with random ASCII fragments
extracted from binary files. The CLI flag `--title` / `--collection`
doesn't help — the user provides a path, gets no error, and only finds
out their collection is junk when search results are nonsense.

**Fix:** Allowlist supported extensions and raise on the rest:

```python
SUPPORTED_EXT = {".pdf", ".txt", ".md", ".markdown"}

def extract_text(path: str) -> str:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_EXT:
        raise ValueError(
            f"Unsupported file type: {ext!r}. Supported: {sorted(SUPPORTED_EXT)}"
        )
    if ext == ".pdf":
        ...
```

The CLI `cmd_ingest` already has a top-level `try/except` in `main()`
that prints the error, so users will see a clean message instead of a
silent corruption.

### WR-06: Orchestrator silently swallows every RAG-side-channel error

**File:** `core/orchestrator/thesis_flow.py:471-480`
**Issue:**
```python
for query in queries:
    try:
        out = await tool.execute(...)
    except Exception:
        continue
    if "error" in out:
        continue
    results.extend(out.get("papers", []))
```
The bare `except Exception: continue` and the
`if "error" in out: continue` both throw away the failure with no log,
no metric, no entry on the blackboard. This is the "soft fallback or
hiding bugs?" question from the brief — it's hiding bugs. If RAG search
silently fails (corrupt collection, embedding model not loaded, qdrant
locked by another process), the user gets a thesis pipeline that
quietly behaves as if `--rag-collection` was never set. They will
think the feature is working and trust the output.

Compare with how the orchestrator handles every other stage: each one
calls `obs_logger.log_event("stage_failed", ...)` with the error string
and appends to `errors`, so the session ends in `status="partial"` and
the user can see what broke.

**Fix:** Mirror the existing per-stage handling:

```python
for query in queries:
    try:
        out = await tool.execute(
            {"query": query, "collection": collection, "limit": 5},
            "public",
        )
    except Exception as e:
        obs_logger.log_event(
            "stage_failed",
            getattr(self, "_session_id", "unknown"),
            {"stage": "rag_search", "query": query, "error": str(e)},
        )
        continue
    if "error" in out:
        obs_logger.log_event(
            "stage_failed",
            getattr(self, "_session_id", "unknown"),
            {"stage": "rag_search", "query": query, "error": out["error"]},
        )
        continue
    results.extend(out.get("papers", []))
```

(Plumbing `session_id` is part of fixing CR-01 — pass it down instead of
relying on instance state.)

### WR-07: Several test assertions are tautological or far too loose to catch regressions

**File:** `tests/test_rag.py:51, 67-72`
**Issue:**

1. **Line 51 — tautological assertion:**
   ```python
   assert chunk.endswith(".") or chunk.endswith(".  ".strip())
   ```
   `".  ".strip()` evaluates to `"."`, so this reduces to
   `chunk.endswith(".") or chunk.endswith(".")`. The whole `or` clause is
   dead. The test will pass even if no chunk ends in a sentence terminator
   (because the check would be false either way and you'd just get an
   `AssertionError` — but the *intent* of having a fallback ending was
   clearly something else, like `chunk.endswith("?")` or
   `chunk.endswith("!")`).

2. **Lines 67-72 — overlap test is degenerate:**
   ```python
   assert nxt.startswith(prev_tail.split()[0]) or any(
       w in nxt.split()[:8] for w in prev_tail.split()
   )
   ```
   The `any(...)` branch passes whenever *any one* of the previous chunk's
   last 5 words appears in the *first 8* words of the next chunk. With
   the test fixture using only ~24 distinct Greek-letter words repeated
   5 times, collisions are guaranteed regardless of whether the overlap
   logic actually works. Replacing the implementation with
   `return [s for s in sentences]` (no overlap at all) would still make
   this pass, since the cyclic Greek-letter words recur naturally.

**Fix:**

For the terminator check, drop the redundant `or` and assert the real
property — every chunk except possibly the last ends at a sentence
boundary:

```python
for chunk in chunks[:-1]:
    assert chunk.rstrip().endswith((".", "!", "?")), \
        f"chunk did not end at sentence boundary: {chunk!r}"
```

For the overlap, use distinguishable tokens and assert the *exact*
contract — the last `overlap_words` of `prev` are the first `overlap_words`
of `nxt`:

```python
def test_chunk_text_overlap_replays_tail():
    sentences = [f"word{i:03d} word{i:03d}b word{i:03d}c word{i:03d}d." for i in range(40)]
    text = " ".join(sentences)
    chunks = chunk_text(text, max_words=20, overlap_words=5)
    assert len(chunks) >= 2
    for prev, nxt in zip(chunks, chunks[1:]):
        prev_tail = prev.split()[-5:]
        nxt_head = nxt.split()[: len(prev_tail)]
        assert nxt_head == prev_tail, (prev_tail, nxt_head)
```

That formulation would actually fail under WR-04's cascade, which is
the point of the test.

## Info

### IN-01: `_create_orchestrator` constructs a `ToolRegistry` even for commands that don't need one

**File:** `apps/cli/main.py:20-32, 64-77, 80-105`
**Issue:** `cmd_critique` and `cmd_verify` both call `_create_orchestrator()`,
which builds the full `ToolRegistry` (including the new `RAGSearchTool`),
the FastEmbed-backed `EpisodicMemory`, the `Router`, the `SessionStore`,
and a `UnifiedLLM`. `cmd_critique` only ever uses `head.execute` and
`cmd_verify` only ever uses the crossref tool. This makes every CLI
invocation pay the FastEmbed model-load cost, which is the same problem
the recent `c056623` commit was working around for the API. Not
introduced by this branch, but the new `RAGSearchTool` adds another
import-time cost (`qdrant_client`, `tools.mcp.rag`) on every command.

**Fix:** Lazily build only what each command needs, e.g. a thin
`_make_critique_pipeline()` that constructs `ThesisHeadProvider` directly.

### IN-02: `chunk_text` sentence splitter has no abbreviation handling

**File:** `tools/mcp/rag.py:63`
**Issue:** `re.split(r"(?<=[.!?])\s+", text)` will split inside
"Mr. Smith", "Fig. 3", "i.e. ", "vs.", and "et al." — common in academic
PDFs. The chunker still produces valid chunks, but the per-sentence word
counts get distorted enough that the `max_words` cap is approximate by
~5–10% in practice. Acceptable for v1; document or revisit if RAG search
quality regresses.

**Fix:** Either ship a small abbreviation list, or note in the docstring
that the splitter is heuristic and undercounts long-running prose.

### IN-03: Deterministic chunk ID derivation includes `chunk[:64]` so trivial edits invalidate it

**File:** `tools/mcp/rag.py:174-177`
**Issue:**
```python
digest = hashlib.md5(
    f"{source_path or source_label}|{idx}|{chunk[:64]}".encode()
).hexdigest()
```
The `chunk[:64]` prefix is included so that *content* changes change the
ID. But it also means that whitespace edits, OCR-rerun differences, or
even appending a single sentence to the source can shift `chunk[:64]` and
re-create *all* downstream chunk IDs as new points instead of overwriting.
The test `test_deterministic_ids_for_same_source_path` only verifies
`n1 == n2` (same chunk count) — it does *not* verify the collection
size stayed the same after the second ingest. So the test name is
misleading and the dedup-by-ID claim in the docstring is only true for
byte-identical re-ingestion.

**Fix:** Either drop `chunk[:64]` from the digest input (then re-ingesting
a byte-identical file truly overwrites; content drift gets resolved at
search time) or actually assert the dedup property in the test:

```python
n1 = indexer.ingest_file(str(src), collection="dedupe-test")
count_before = in_memory_client.count(_collection_for("dedupe-test")).count
n2 = indexer.ingest_file(str(src), collection="dedupe-test")
count_after = in_memory_client.count(_collection_for("dedupe-test")).count
assert n1 == n2
assert count_before == count_after  # this is the actual dedup claim
```

MD5 is fine here — it's not a security boundary, just a hash for ID
derivation, and `uuid.UUID(md5_hex)` is a clean way to coerce to UUID.
No security concern.

### IN-04: `EMBEDDING_MODEL` constant duplicates a string literal already in three other modules

**File:** `tools/mcp/rag.py:35`
**Issue:** `"BAAI/bge-small-en-v1.5"` is hard-coded in
`core/memory/episodic.py:27`, `core/corpus/ingest.py:153`,
`core/corpus/retrieve.py:20`, and now `tools/mcp/rag.py:35`. Pre-existing
duplication, but this branch is a good moment to lift it into a single
constant (e.g. `core/embeddings.py: EMBEDDING_MODEL = ...`).

**Fix:** Centralise the constant in one place, import it from the others.
Out of scope for this branch if the author wants to keep the diff
additive — flag for a follow-up.

### IN-05: `cmd_ingest` doesn't validate `--max-words` / `--overlap` before passing them down

**File:** `apps/cli/main.py:228-233`
**Issue:** The CLI accepts `--max-words 0` or `--overlap 999`, then hands
them straight to `RAGIndexer.ingest_file` → `chunk_text`, which raises
`ValueError`. The user sees `Error: max_words must be positive` from the
top-level `except` in `main()` — clean enough — but the validation is
sitting inside `chunk_text` instead of at the argparse layer, where it
belongs and where it could give a more pointed message.

**Fix:** Use argparse `type=` validators or a `--max-words` post-parse
check in `cmd_ingest`. Low priority.

### IN-06: `RAGSearchTool.execute_impl` does blocking I/O inside an async coroutine

**File:** `tools/mcp/rag.py:309-313`
**Issue:**
```python
results = client.query(
    collection_name=coll,
    query_text=query,
    limit=limit,
)
```
`QdrantClient.query` (with the local `path=...` backend and FastEmbed)
is synchronous and CPU-bound (embedding) + blocking I/O (RocksDB read).
Running it directly in an `async def` blocks the event loop. The brief
notes that "the academic tools already use this pattern" — confirmed,
e.g. `WebSearchTool` uses `asyncio.to_thread(_search)` whereas the
academic tools call sync libraries directly. RAG inherits the same
sin.

This isn't a correctness bug today (the CLI awaits one task at a time),
but the same orchestrator runs under `apps/api/main.py` where multiple
requests share the event loop, and one slow RAG query will stall every
other in-flight handler.

**Fix:** Wrap in `asyncio.to_thread`:

```python
async def execute_impl(self, params: Dict[str, Any]) -> Dict[str, Any]:
    ...
    try:
        results = await asyncio.to_thread(
            client.query,
            collection_name=coll,
            query_text=query,
            limit=limit,
        )
    except Exception as exc:
        ...
```

Out of v1 perf scope per the review brief, kept here because the issue
is *correctness under concurrency* (event-loop starvation), not raw
throughput.

---

_Reviewed: 2026-05-08_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
