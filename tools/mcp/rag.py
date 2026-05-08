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
collections. Names containing characters outside ``[A-Za-z0-9_-]`` are
disambiguated with a 6-char hash suffix so two distinct user inputs cannot
collapse to the same backing collection (review finding WR-01).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional

from qdrant_client import QdrantClient

from core.embeddings import EMBEDDING_MODEL
from tools.mcp.engine import MCPTool

logger = logging.getLogger(__name__)

COLLECTION_PREFIX = "rag_"

SUPPORTED_EXTENSIONS = (".pdf", ".txt", ".md", ".markdown")

# Common abbreviations that should not terminate a sentence. Heuristic — keeps
# the splitter cheap. Long-form prose with esoteric abbreviations may still
# undercount, but the chunker is robust to that (see chunk_text WR-02 fix).
_ABBREV = (
    "mr",
    "mrs",
    "ms",
    "dr",
    "prof",
    "fig",
    "eq",
    "no",
    "vol",
    "vs",
    "etc",
    "i.e",
    "e.g",
    "et al",
    "cf",
    "approx",
    "inc",
    "ltd",
)


def _collection_for(name: str) -> str:
    """Map a user-facing collection name to a backing Qdrant collection name.

    Sanitisation collapses characters outside ``[A-Za-z0-9_-]`` into
    underscores. To prevent two distinct inputs (e.g. ``../etc/passwd`` vs
    ``etc/passwd``) collapsing onto the same backing collection — which would
    let one user's typo wipe another user's data via ``ingest delete`` — any
    name that required sanitisation is disambiguated with a 6-char hash of
    the original input. Already-safe names map identically.
    """
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", name).strip("_")
    if not safe:
        return f"{COLLECTION_PREFIX}default"
    if safe == name:
        return f"{COLLECTION_PREFIX}{safe}"
    suffix = hashlib.md5(name.encode("utf-8")).hexdigest()[:6]
    return f"{COLLECTION_PREFIX}{safe}_{suffix}"


def _is_sentence_terminator(words_so_far: List[str]) -> bool:
    """True if the last word in ``words_so_far`` is a real sentence ender.

    Skips terminators that look like abbreviations (``Dr.``, ``i.e.``, etc.).
    """
    if not words_so_far:
        return False
    last = words_so_far[-1].rstrip(")]\"'").lower()
    if not last or last[-1] not in ".!?":
        return False
    stem = last.rstrip(".!?")
    return stem not in _ABBREV


def _split_sentences(text: str) -> List[str]:
    """Sentence-split with light abbreviation handling.

    Splits on ``.!?`` followed by whitespace, then re-glues fragments where
    the previous fragment ends with a known abbreviation.
    """
    raw = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not raw:
        return []

    merged: List[str] = []
    for piece in raw:
        if merged and _ends_with_abbrev(merged[-1]):
            merged[-1] = merged[-1] + " " + piece
        else:
            merged.append(piece)
    return merged


def _ends_with_abbrev(sentence: str) -> bool:
    last = sentence.split()[-1] if sentence.split() else ""
    stem = last.rstrip(".!?").lower()
    return stem in _ABBREV


def chunk_text(
    text: str,
    max_words: int = 300,
    overlap_words: int = 50,
) -> List[str]:
    """Sentence-aware fixed-size chunking with word overlap.

    Splits on ``.!?`` boundaries (with abbreviation guards), then packs
    sentences into chunks up to ``max_words`` long. The next chunk replays
    the last ``overlap_words`` words from the previous one so retrieval near
    chunk boundaries still surfaces the right context.

    A single sentence longer than ``max_words`` (common in PDF exports of
    bibliographies and equations) is hard-split into word-windows of
    ``max_words`` rather than allowed to balloon a single chunk arbitrarily.
    """
    if not text or not text.strip():
        return []
    if max_words <= 0:
        raise ValueError("max_words must be positive")
    if overlap_words < 0 or overlap_words >= max_words:
        raise ValueError("overlap_words must satisfy 0 <= overlap < max_words")

    sentences = _split_sentences(text)
    if not sentences:
        return []

    step = max_words - overlap_words

    chunks: List[str] = []
    buf: List[str] = []  # current chunk's sentences
    buf_words = 0
    carry: List[str] = []  # words to prepend to the next chunk (true overlap)

    def flush() -> None:
        nonlocal buf, buf_words, carry
        if not buf:
            return
        if carry:
            chunk = " ".join(carry) + " " + " ".join(buf)
        else:
            chunk = " ".join(buf)
        chunks.append(chunk)
        # Carry forward only from the freshly-finalised buf, never from
        # previous carries — prevents the overlap cascade flagged in WR-04.
        flat = " ".join(buf).split()
        carry = flat[-overlap_words:] if overlap_words and flat else []
        buf = []
        buf_words = 0

    for sentence in sentences:
        words = sentence.split()
        if not words:
            continue

        # WR-02: hard-split a single oversized sentence into word-windows so
        # no chunk exceeds max_words. Step-based indexing provides overlap
        # internally; external carry would inflate chunk size beyond max_words.
        if len(words) > max_words:
            flush()
            carry = []
            for i in range(0, len(words), step):
                window = words[i : i + max_words]
                if not window:
                    continue
                chunks.append(" ".join(window))
                carry = window[-overlap_words:] if overlap_words else []
            continue

        if buf_words + len(words) > max_words:
            flush()

        buf.append(sentence)
        buf_words += len(words)

    flush()
    return chunks


def extract_text(path: str) -> str:
    """Pull plain text from a supported file.

    Supported extensions are listed in ``SUPPORTED_EXTENSIONS``. Other types
    (``.docx``, ``.exe``, images, archives) raise ``ValueError`` to prevent
    the caller from silently embedding garbage extracted from binary content
    via ``errors="ignore"`` (review finding WR-05).

    Text files are decoded with ``utf-8-sig`` so any UTF-8 BOM is stripped
    during decode rather than via a fragile post-hoc ``lstrip`` on the
    codepoint U+FEFF (review finding WR-03).
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type: {ext!r}. "
            f"Supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )

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

    with open(path, encoding="utf-8-sig", errors="ignore") as f:
        return f.read()


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
        # IN-03: id derivation no longer includes a chunk-content prefix, so
        # re-ingesting a byte-identical file truly overwrites the existing
        # points instead of inserting near-duplicates whenever whitespace or
        # OCR drift shifts chunk[:64].
        key_base = source_path or source_label
        for idx, chunk in enumerate(chunks):
            digest = hashlib.md5(f"{key_base}|{idx}".encode("utf-8")).hexdigest()
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

    The synchronous ``QdrantClient.query`` call (FastEmbed embedding +
    RocksDB read) is dispatched via ``asyncio.to_thread`` so it does not
    starve the event loop under the API server (review finding IN-06).
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
            results = await asyncio.to_thread(
                client.query,
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
