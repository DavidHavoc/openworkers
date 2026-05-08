"""Tests for the user RAG ingest + search path.

All tests run under DRY_RUN=true and never hit external services. The
QdrantClient is constructed with ``location=":memory:"`` (the same
in-memory mode the existing memory/episodic tests rely on).
"""

from __future__ import annotations

import os

import pytest
from qdrant_client import QdrantClient

os.environ.setdefault("DRY_RUN", "true")
os.environ.pop("QDRANT_URL", None)

from core.embeddings import EMBEDDING_MODEL  # noqa: E402
from tools.mcp.rag import (  # noqa: E402  -- env vars must be set before import
    COLLECTION_PREFIX,
    SUPPORTED_EXTENSIONS,
    RAGIndexer,
    RAGSearchTool,
    _collection_for,
    chunk_text,
    extract_text,
)


# ──────────────────────────────────────────────────────────────────────────
# chunking
# ──────────────────────────────────────────────────────────────────────────


def test_chunk_text_empty_input():
    assert chunk_text("") == []
    assert chunk_text("   \n\n\t  ") == []


def test_chunk_text_single_chunk_for_short_text():
    text = "Photonic computing aims to replace electrons with photons. It promises lower energy use."
    chunks = chunk_text(text, max_words=300, overlap_words=50)
    assert len(chunks) == 1
    assert "Photonic" in chunks[0]


def test_chunk_text_splits_at_max_words_with_sentence_boundary():
    """Every chunk except possibly the last ends at a real sentence terminator.

    Replaces the previous tautological `chunk.endswith(".  ".strip())` check
    (review finding WR-07).
    """
    sentence = "This is a sentence with several words that we will repeat. "
    text = sentence * 50  # 50 sentences x 11 words = 550 words
    chunks = chunk_text(text, max_words=100, overlap_words=20)
    assert len(chunks) > 1
    for chunk in chunks[:-1]:
        stripped = chunk.rstrip()
        assert stripped.endswith((".", "!", "?")), (
            f"chunk did not end at sentence boundary: {stripped[-30:]!r}"
        )


def test_chunk_text_oversized_sentence_is_hard_split(capsys):
    """A single sentence longer than max_words must not produce one giant chunk.

    Regression for review finding WR-02 — bibliography lines and equation
    captions in PDFs routinely run hundreds of words without a `.!?` break.
    """
    long_sentence = " ".join(f"word{i:04d}" for i in range(800))  # one "sentence"
    chunks = chunk_text(long_sentence, max_words=100, overlap_words=20)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.split()) <= 100, (
            f"chunk exceeded max_words cap: {len(chunk.split())} words"
        )


def test_chunk_text_overlap_replays_exact_tail():
    """The last `overlap_words` of chunk N are the first `overlap_words` of chunk N+1.

    Replaces the previous degenerate test that passed even if overlap was
    deleted entirely (review finding WR-07). Uses distinguishable tokens so
    accidental cyclic collisions can't satisfy the assertion.
    """
    sentences = [
        f"alpha{i:03d} beta{i:03d} gamma{i:03d} delta{i:03d}." for i in range(40)
    ]
    text = " ".join(sentences)
    chunks = chunk_text(text, max_words=20, overlap_words=5)
    assert len(chunks) >= 2
    for prev, nxt in zip(chunks, chunks[1:]):
        prev_tail = prev.split()[-5:]
        nxt_head = nxt.split()[:5]
        assert nxt_head == prev_tail, (
            f"overlap broken: prev_tail={prev_tail!r} nxt_head={nxt_head!r}"
        )


def test_chunk_text_overlap_does_not_cascade():
    """Words from chunk N must not bleed into chunk N+2.

    Regression for review finding WR-04 — the previous overlap implementation
    re-fed the carried tail back into the buffer, so words from chunk N could
    appear in chunk N+2 when sentences were short.
    """
    sentences = [
        f"sentinel{i:03d} marker{i:03d} token{i:03d}." for i in range(60)
    ]
    text = " ".join(sentences)
    chunks = chunk_text(text, max_words=12, overlap_words=3)
    assert len(chunks) >= 3
    for n in range(len(chunks) - 2):
        chunk_n_words = set(chunks[n].split())
        chunk_n2_words = set(chunks[n + 2].split())
        # The first 3 sentinel tokens of chunk N (sentinel000 etc.) must not
        # appear in chunk N+2, since overlap should only carry one chunk
        # forward.
        sentinels_in_n = {w for w in chunk_n_words if w.startswith("sentinel")}
        sentinels_in_n2 = {w for w in chunk_n2_words if w.startswith("sentinel")}
        assert sentinels_in_n.isdisjoint(sentinels_in_n2), (
            f"chunk {n} sentinels leaked into chunk {n + 2}: "
            f"{sentinels_in_n & sentinels_in_n2}"
        )


def test_chunk_text_rejects_invalid_overlap():
    with pytest.raises(ValueError):
        chunk_text("hello world", max_words=10, overlap_words=10)
    with pytest.raises(ValueError):
        chunk_text("hello world", max_words=0)


def test_chunk_text_handles_abbreviations():
    """Common abbreviations like 'Fig. 3' don't fragment a sentence.

    Heuristic — IN-02 only ships a small abbrev list, so this is a smoke
    test that the most common case works, not a guarantee for arbitrary
    abbreviations.
    """
    text = "See Fig. 3 for the experimental setup. Results are in Table 2."
    chunks = chunk_text(text, max_words=300, overlap_words=20)
    assert len(chunks) == 1
    assert "Fig. 3" in chunks[0]
    assert "Table 2" in chunks[0]


# ──────────────────────────────────────────────────────────────────────────
# extract_text
# ──────────────────────────────────────────────────────────────────────────


def test_extract_text_reads_text_file(tmp_path):
    p = tmp_path / "note.txt"
    p.write_text("hello world\nsecond line", encoding="utf-8")
    assert extract_text(str(p)) == "hello world\nsecond line"


def test_extract_text_strips_bom(tmp_path):
    """Leading UTF-8 BOM is removed during decode (utf-8-sig).

    Regression for review finding WR-03 — the previous implementation used
    `.lstrip("﻿")` which happened to work but treated the BOM as a *set* of
    characters rather than a substring, and depended on a literal U+FEFF
    sitting in the source file (some editors silently strip those).
    """
    p = tmp_path / "bom.txt"
    p.write_bytes(b"\xef\xbb\xbfwith bom")
    assert extract_text(str(p)) == "with bom"


def test_extract_text_supports_markdown(tmp_path):
    p = tmp_path / "note.md"
    p.write_text("# Heading\n\nbody text.", encoding="utf-8")
    assert "Heading" in extract_text(str(p))


def test_extract_text_rejects_unknown_extension(tmp_path):
    """Binary / unsupported file types raise rather than silently embedding garbage.

    Regression for review finding WR-05.
    """
    p = tmp_path / "data.bin"
    p.write_bytes(b"\x00\x01\x02\x03not real text")
    with pytest.raises(ValueError, match="Unsupported file type"):
        extract_text(str(p))


def test_extract_text_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract_text(str(tmp_path / "nope.txt"))


def test_supported_extensions_includes_expected_set():
    assert ".pdf" in SUPPORTED_EXTENSIONS
    assert ".txt" in SUPPORTED_EXTENSIONS
    assert ".md" in SUPPORTED_EXTENSIONS


# ──────────────────────────────────────────────────────────────────────────
# collection naming
# ──────────────────────────────────────────────────────────────────────────


def test_collection_for_namespaces_under_prefix():
    assert _collection_for("MyDocs").startswith(COLLECTION_PREFIX)
    assert _collection_for("MyDocs") == "rag_MyDocs"


def test_collection_for_safe_names_pass_through_unchanged():
    """Names that are already in [A-Za-z0-9_-] map identically to rag_<name>."""
    for safe_name in ("alpha", "beta-1", "my_collection_2025", "a-b_c"):
        assert _collection_for(safe_name) == f"{COLLECTION_PREFIX}{safe_name}"


def test_collection_for_disambiguates_unsafe_names():
    """Two distinct unsafe inputs that sanitise to the same stem must map to
    different backing collections (review finding WR-01).

    Previously, `../etc/passwd` and `etc/passwd` both collapsed to
    `rag_etc_passwd`, which let one user's typo destroy another user's
    collection via `thesis ingest delete`.
    """
    a = _collection_for("../etc/passwd")
    b = _collection_for("etc/passwd")
    c = _collection_for("etc!passwd")
    # All three sanitise to the same `etc_passwd` stem, but each gets a
    # distinct hash suffix derived from the original input.
    assert a != b
    assert b != c
    assert a != c
    assert all(s.startswith("rag_etc_passwd_") for s in (a, b, c))


def test_collection_for_empty_after_sanitize_returns_default():
    assert _collection_for("") == "rag_default"
    assert _collection_for("!!!") == "rag_default"
    assert _collection_for("   ") == "rag_default"


# ──────────────────────────────────────────────────────────────────────────
# indexer + tool round-trip
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def in_memory_client() -> QdrantClient:
    client = QdrantClient(location=":memory:")
    client.set_model(EMBEDDING_MODEL)
    return client


def test_indexer_round_trip(tmp_path, in_memory_client):
    src = tmp_path / "doc.txt"
    src.write_text(
        "Photonic computing aims to replace electrons with photons in CPUs. "
        "Photons have no rest mass and can travel at the speed of light, "
        "but optical switches are slower than transistors and require careful thermal control. "
        "Recent prototypes show promising results for matrix multiplication workloads.",
        encoding="utf-8",
    )

    indexer = RAGIndexer(client=in_memory_client)
    n = indexer.ingest_file(str(src), collection="testcol", title="photonics-note")
    assert n >= 1

    coll = _collection_for("testcol")
    assert in_memory_client.collection_exists(collection_name=coll)

    # The same client wired through the tool finds the chunk.
    tool = RAGSearchTool(client=in_memory_client)

    import asyncio

    out = asyncio.run(tool.execute({"query": "photonic CPUs", "collection": "testcol"}, "public"))
    assert "papers" in out
    assert out["total_results"] >= 1
    paper = out["papers"][0]
    assert paper["source"] == "rag"
    assert paper["paper_id"]
    assert "photon" in paper["abstract"].lower()
    assert paper["title"].startswith("photonics-note")


def test_search_returns_empty_for_unknown_collection(in_memory_client):
    tool = RAGSearchTool(client=in_memory_client)
    import asyncio

    out = asyncio.run(
        tool.execute({"query": "anything", "collection": "never_ingested"}, "public")
    )
    assert out == {"papers": [], "total_results": 0}


def test_search_rejects_blocked_privacy_tier(in_memory_client):
    tool = RAGSearchTool(client=in_memory_client)
    tool.allowed_tiers = ["trusted"]
    import asyncio

    out = asyncio.run(tool.execute({"query": "x", "collection": "y"}, "public"))
    assert "error" in out
    assert "Security Violation" in out["error"]


def test_indexer_list_and_delete(tmp_path, in_memory_client):
    src = tmp_path / "doc.txt"
    src.write_text("alpha beta gamma. delta epsilon zeta.", encoding="utf-8")

    indexer = RAGIndexer(client=in_memory_client)
    indexer.ingest_file(str(src), collection="alpha")
    indexer.ingest_file(str(src), collection="beta")

    names = indexer.list_collections()
    assert "alpha" in names
    assert "beta" in names

    assert indexer.delete_collection("alpha") is True
    assert indexer.delete_collection("alpha") is False  # second time: gone
    assert "alpha" not in indexer.list_collections()


def test_re_ingest_does_not_grow_collection(tmp_path, in_memory_client):
    """Re-ingesting a byte-identical file must not double the collection size.

    Replaces the previous test that only verified `n1 == n2` (chunk count)
    without actually checking that the *collection* didn't grow — the dedup
    claim in the docstring (review finding IN-03).
    """
    src = tmp_path / "doc.txt"
    src.write_text(
        "First paragraph about photonics. Second paragraph with more detail. "
        "Third paragraph wraps it up.",
        encoding="utf-8",
    )

    indexer = RAGIndexer(client=in_memory_client)
    n1 = indexer.ingest_file(str(src), collection="dedupe-test")
    coll = _collection_for("dedupe-test")
    count_after_first = in_memory_client.count(collection_name=coll).count

    n2 = indexer.ingest_file(str(src), collection="dedupe-test")
    count_after_second = in_memory_client.count(collection_name=coll).count

    assert n1 == n2 >= 1
    # The actual dedup property — same source path, same chunk indices,
    # same point IDs, so the second ingest overwrites in place.
    assert count_after_first == count_after_second, (
        f"collection grew on re-ingest: {count_after_first} -> {count_after_second}"
    )
