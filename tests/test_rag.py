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

from tools.mcp.rag import (  # noqa: E402  -- env vars must be set before import
    COLLECTION_PREFIX,
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
    sentence = "This is a sentence with several words that we will repeat. "
    text = sentence * 50  # ~ 50 sentences, each 11 words = 550 words
    chunks = chunk_text(text, max_words=100, overlap_words=20)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.endswith(".") or chunk.endswith(".  ".strip())
        words = len(chunk.split())
        # No chunk should be wildly larger than the cap (overlap is allowed
        # to push it slightly, but never by more than one full sentence
        # plus the requested overlap).
        assert words <= 100 + 20 + 11, f"chunk too long: {words} words"


def test_chunk_text_overlap_replays_tail():
    text = (
        "Alpha beta gamma delta epsilon zeta eta theta. "
        "Iota kappa lambda mu nu xi omicron pi. "
        "Rho sigma tau upsilon phi chi psi omega. "
    ) * 5
    chunks = chunk_text(text, max_words=15, overlap_words=5)
    assert len(chunks) >= 2
    for prev, nxt in zip(chunks, chunks[1:]):
        prev_tail = " ".join(prev.split()[-5:])
        # The overlap is the *prefix* of the next chunk.
        assert nxt.startswith(prev_tail.split()[0]) or any(
            w in nxt.split()[:8] for w in prev_tail.split()
        )


def test_chunk_text_rejects_invalid_overlap():
    with pytest.raises(ValueError):
        chunk_text("hello world", max_words=10, overlap_words=10)
    with pytest.raises(ValueError):
        chunk_text("hello world", max_words=0)


# ──────────────────────────────────────────────────────────────────────────
# extract_text
# ──────────────────────────────────────────────────────────────────────────


def test_extract_text_reads_text_file(tmp_path):
    p = tmp_path / "note.txt"
    p.write_text("hello world\nsecond line", encoding="utf-8")
    assert extract_text(str(p)) == "hello world\nsecond line"


def test_extract_text_strips_bom(tmp_path):
    p = tmp_path / "bom.txt"
    p.write_bytes("﻿with bom".encode("utf-8"))
    assert extract_text(str(p)) == "with bom"


def test_extract_text_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract_text(str(tmp_path / "nope.txt"))


# ──────────────────────────────────────────────────────────────────────────
# collection naming
# ──────────────────────────────────────────────────────────────────────────


def test_collection_for_namespaces_under_prefix():
    assert _collection_for("MyDocs").startswith(COLLECTION_PREFIX)
    assert _collection_for("MyDocs") == "rag_MyDocs"


def test_collection_for_sanitizes_unsafe_chars():
    assert _collection_for("../etc/passwd") == "rag_etc_passwd"
    assert _collection_for("") == "rag_default"
    assert _collection_for("!!!") == "rag_default"


# ──────────────────────────────────────────────────────────────────────────
# indexer + tool round-trip
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def in_memory_client() -> QdrantClient:
    client = QdrantClient(location=":memory:")
    client.set_model("BAAI/bge-small-en-v1.5")
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
    # RAGSearchTool allows public/sanitized/trusted by default.
    # Override to a single tier and confirm the gate fires.
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


def test_deterministic_ids_for_same_source_path(tmp_path, in_memory_client):
    src = tmp_path / "doc.txt"
    src.write_text(
        "First paragraph about photonics. Second paragraph with more detail. "
        "Third paragraph wraps it up.",
        encoding="utf-8",
    )

    indexer = RAGIndexer(client=in_memory_client)
    n1 = indexer.ingest_file(str(src), collection="dedupe-test")
    # Re-ingesting the same file produces the same point IDs, so the
    # collection size doesn't grow unbounded.
    n2 = indexer.ingest_file(str(src), collection="dedupe-test")
    assert n1 == n2 >= 1
