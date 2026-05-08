"""Single source of truth for the FastEmbed model used across the project.

Previously hard-coded as ``"BAAI/bge-small-en-v1.5"`` in four separate files
(``core.memory.episodic``, ``core.corpus.ingest``, ``core.corpus.retrieve``,
``tools.mcp.rag``). Centralised here so a future model bump touches one line.
"""

from __future__ import annotations

EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
"""FastEmbed model used for episodic memory, the thesis corpus, and user RAG.

All three Qdrant collections share this model so vectors are dimensionally
compatible — never vary it per-collection without also handling the dimension
mismatch."""
