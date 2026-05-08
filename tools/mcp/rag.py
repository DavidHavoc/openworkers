"""RAG over user-provided PDFs and text files.

Mirrors the Qdrant + FastEmbed pattern already used by core.corpus.* and
core.memory.episodic so we don't introduce a second embedding stack.

Two public surfaces:

* ``RAGIndexer`` — script-side: chunk a PDF/text file and write to Qdrant.
* ``RAGSearchTool`` — orchestrator-side: a regular ``MCPTool`` that the
  ``ToolRegistry`` exposes alongside arxiv/semantic_scholar/crossref. Returns
  results in the same shape as the academic tools (a ``papers`` list with
  ``paper_id``, ``title``, ``abstract``, ``source``, …) so the existing
  researcher / lit_map pipeline accepts them without changes.

Collections are namespaced under ``rag_<name>`` so RAG storage cannot collide
with the ``thesis_corpus`` (corpus benchmarks) or ``episodes`` (memory)
collections.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional

from qdrant_client import QdrantClient

from tools.mcp.engine import MCPTool

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
COLLECTION_PREFIX = "rag_"


def _collection_for(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", name).strip("_") or "default"
    return f"{COLLECTION_PREFIX}{safe}"


def chunk_text(
    text: str,
    max_words: int = 300,
    overlap_words: int = 50,
) -> List[str]:
    """Sentence-aware fixed-size chunking with word overlap.

    Splits on ``.!?`` boundaries, then packs sentences into chunks up to
    ``max_words`` long. The next chunk replays the last ``overlap_words``
    words from the previous one so retrieval near chunk boundaries still
    surfaces the right context.
    """
    if not text or not text.strip():
        return []
    if max_words <= 0:
        raise ValueError("max_words must be positive")
    if overlap_words < 0 or overlap_words >= max_words:
        raise ValueError("overlap_words must satisfy 0 <= overlap < max_words")

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not sentences:
        return []

    chunks: List[str] = []
    buf: List[str] = []
    buf_words = 0

    for sentence in sentences:
        words = sentence.split()
        if not words:
            continue
        if buf_words + len(words) > max_words and buf:
            chunks.append(" ".join(buf))
            if overlap_words:
                tail_words: List[str] = []
                for prev in reversed(buf):
                    tail_words = prev.split() + tail_words
                    if len(tail_words) >= overlap_words:
                        break
                tail = " ".join(tail_words[-overlap_words:])
                buf = [tail] if tail else []
                buf_words = len(buf[0].split()) if buf else 0
            else:
                buf = []
                buf_words = 0
        buf.append(sentence)
        buf_words += len(words)

    if buf:
        chunks.append(" ".join(buf))
    return chunks


def extract_text(path: str) -> str:
    """Pull plain text from a PDF or text/markdown file.

    PDF extraction uses PyMuPDF (already a project dependency). For .txt and
    .md we just read the file and strip BOMs.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        try:
            import fitz  # PyMuPDF
        except ImportError as exc:  # pragma: no cover - declared dep
            raise RuntimeError(
                "PyMuPDF (pymupdf) is required to ingest PDFs. Reinstall with `pip install -e .`"
            ) from exc
        doc = fitz.open(path)
        try:
            return "\n".join(page.get_text() for page in doc)
        finally:
            doc.close()

    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read().lstrip("﻿")


def _build_qdrant(qdrant_path: str = "./qdrant_data") -> QdrantClient:
    qdrant_url = os.environ.get("QDRANT_URL")
    if qdrant_url:
        client = QdrantClient(url=qdrant_url)
    elif qdrant_path == ":memory:":
        client = QdrantClient(location=":memory:")
    else:
        client = QdrantClient(path=qdrant_path)
    client.set_model(EMBEDDING_MODEL)
    return client


class RAGIndexer:
    """Indexes user-supplied PDFs/text into a namespaced Qdrant collection."""

    def __init__(
        self,
        client: Optional[QdrantClient] = None,
        qdrant_path: str = "./qdrant_data",
    ) -> None:
        self.client = client or _build_qdrant(qdrant_path)

    def ensure_collection(self, collection: str) -> str:
        coll = _collection_for(collection)
        if not self.client.collection_exists(collection_name=coll):
            self.client.create_collection(
                collection_name=coll,
                vectors_config=self.client.get_fastembed_vector_params(),
            )
        return coll

    def ingest_text(
        self,
        text: str,
        *,
        collection: str,
        source_label: str,
        source_path: str = "",
        max_words: int = 300,
        overlap_words: int = 50,
    ) -> int:
        chunks = chunk_text(text, max_words=max_words, overlap_words=overlap_words)
        if not chunks:
            return 0

        coll = self.ensure_collection(collection)
        ids: List[str] = []
        documents: List[str] = []
        metadata: List[Dict[str, Any]] = []
        for idx, chunk in enumerate(chunks):
            digest = hashlib.md5(
                f"{source_path or source_label}|{idx}|{chunk[:64]}".encode()
            ).hexdigest()
            ids.append(str(uuid.UUID(digest)))
            documents.append(chunk)
            metadata.append(
                {
                    "source_label": source_label,
                    "source_path": source_path,
                    "chunk_index": idx,
                    "total_chunks": len(chunks),
                }
            )
        self.client.add(
            collection_name=coll,
            documents=documents,
            metadata=metadata,
            ids=ids,
        )
        return len(chunks)

    def ingest_file(
        self,
        path: str,
        *,
        collection: str,
        title: str = "",
        max_words: int = 300,
        overlap_words: int = 50,
    ) -> int:
        text = extract_text(path)
        label = title or os.path.basename(path)
        return self.ingest_text(
            text,
            collection=collection,
            source_label=label,
            source_path=os.path.abspath(path),
            max_words=max_words,
            overlap_words=overlap_words,
        )

    def list_collections(self) -> List[str]:
        names: List[str] = []
        for c in self.client.get_collections().collections:
            if c.name.startswith(COLLECTION_PREFIX):
                names.append(c.name[len(COLLECTION_PREFIX):])
        return sorted(names)

    def delete_collection(self, collection: str) -> bool:
        coll = _collection_for(collection)
        if not self.client.collection_exists(collection_name=coll):
            return False
        self.client.delete_collection(collection_name=coll)
        return True


class RAGSearchTool(MCPTool):
    """MCPTool for querying user RAG collections.

    Returns ``{"papers": [...]}`` shaped exactly like the academic tools so the
    orchestrator's existing ``_search_literature_from_plan`` /
    ``_deduplicate_papers`` pipeline can consume the output unchanged.
    """

    name = "rag_search"
    description = (
        "Search a user-ingested PDF/text RAG collection. Returns chunks shaped "
        "as papers so the lit_map pipeline can ingest them."
    )
    allowed_tiers = ["public", "sanitized", "trusted"]
    timeout = 15

    def __init__(
        self,
        client: Optional[QdrantClient] = None,
        qdrant_path: str = "./qdrant_data",
    ) -> None:
        self._client = client
        self._qdrant_path = qdrant_path

    def _get_client(self) -> QdrantClient:
        if self._client is None:
            self._client = _build_qdrant(self._qdrant_path)
        return self._client

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "collection": {
                    "type": "string",
                    "description": "User collection name (without rag_ prefix)",
                    "default": "default",
                },
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        }

    def get_output_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "papers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "paper_id": {"type": "string"},
                            "title": {"type": "string"},
                            "abstract": {"type": "string"},
                            "url": {"type": "string"},
                            "source": {"type": "string"},
                            "score": {"type": "number"},
                        },
                    },
                },
                "total_results": {"type": "integer"},
            },
        }

    async def execute_impl(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = params.get("query", "")
        collection = params.get("collection") or "default"
        limit = int(params.get("limit", 5))
        if not query:
            return {"papers": [], "total_results": 0}

        coll = _collection_for(collection)
        client = self._get_client()
        if not client.collection_exists(collection_name=coll):
            return {"papers": [], "total_results": 0}

        try:
            results = client.query(
                collection_name=coll,
                query_text=query,
                limit=limit,
            )
        except Exception as exc:  # pragma: no cover - depends on backend
            logger.warning("RAG query failed for collection %s: %s", coll, exc)
            return {"papers": [], "total_results": 0, "error": str(exc)}

        papers: List[Dict[str, Any]] = []
        for r in results:
            meta = r.metadata or {}
            label = meta.get("source_label", "rag_chunk")
            chunk_idx = meta.get("chunk_index", 0)
            source_path = meta.get("source_path", "")
            papers.append(
                {
                    "paper_id": str(r.id),
                    "title": f"{label} (chunk {chunk_idx})",
                    "authors": [],
                    "year": 0,
                    "abstract": r.document or "",
                    "url": f"file://{source_path}" if source_path else "",
                    "source": "rag",
                    "score": float(getattr(r, "score", 0.0) or 0.0),
                }
            )
        return {"papers": papers, "total_results": len(papers)}
