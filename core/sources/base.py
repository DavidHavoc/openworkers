from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class EvidenceSnippet:
    """A single piece of evidence retrieved from a source.

    ``path`` is opaque to the adapter contract — it might be a file path,
    a URL, a DOI, or a chunk id. The synthesizer treats it as a citation
    handle: anything a human can navigate back to.
    """

    path: str
    line_start: int
    line_end: int
    text: str
    source: str = ""

    def cite(self) -> str:
        if self.line_start <= 0:
            return self.path
        if self.line_end and self.line_end != self.line_start:
            return f"{self.path}:{self.line_start}-{self.line_end}"
        return f"{self.path}:{self.line_start}"


class SourceAdapter(ABC):
    """Abstract contract every evidence backend implements."""

    name: str = "unknown"

    @abstractmethod
    def search(self, query: str, limit: int = 5) -> list[EvidenceSnippet]:
        """Return up to ``limit`` snippets relevant to ``query``."""

    def fetch(self, path: str, line_start: int = 0, line_end: int = 0) -> EvidenceSnippet:
        """Return the canonical content for a citation handle.

        Default implementation: callers that only need search results can
        ignore this; adapters that support deep-linked retrieval override.
        """
        return EvidenceSnippet(
            path=path,
            line_start=line_start,
            line_end=line_end,
            text="",
            source=self.name,
        )
