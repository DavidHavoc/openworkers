from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from core.sources.base import EvidenceSnippet, SourceAdapter

_DEFAULT_INCLUDE_SUFFIXES = {
    ".py",
    ".pyi",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".rb",
    ".php",
    ".cs",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".swift",
    ".sh",
    ".bash",
    ".zsh",
    ".sql",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".ini",
    ".cfg",
    ".md",
    ".rst",
    ".txt",
    ".env",
    ".dockerfile",
    ".tf",
}

_DEFAULT_EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "qdrant_data",
}

_MAX_BYTES_PER_FILE = 256 * 1024
_CONTEXT_LINES = 2


class LocalRepoAdapter(SourceAdapter):
    """Evidence backend that grep-walks a repo from the filesystem.

    Why not shell out to ripgrep: we want zero external deps for the
    smoke path; a pure-Python walker is fast enough on the kinds of
    READMEs we audit (a few dozen claims, repos under a few thousand
    files). Swap in ripgrep later if profiling demands it.
    """

    name = "local_repo"

    def __init__(
        self,
        root: Path | str,
        include_suffixes: set[str] | None = None,
        exclude_dirs: set[str] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        if not self.root.exists():
            raise FileNotFoundError(f"Repo root does not exist: {self.root}")
        self.include_suffixes = include_suffixes or _DEFAULT_INCLUDE_SUFFIXES
        self.exclude_dirs = exclude_dirs or _DEFAULT_EXCLUDE_DIRS

    def search(self, query: str, limit: int = 5) -> list[EvidenceSnippet]:
        query = (query or "").strip()
        if not query:
            return []
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        return self._search_pattern(pattern, limit)

    def search_any(self, terms: Iterable[str], limit: int = 5) -> list[EvidenceSnippet]:
        """Search for *any* of ``terms``. Useful when the planner emits
        multiple search hints per claim — we want to retrieve evidence
        when any hint matches, not require all.
        """
        cleaned = [re.escape(t.strip()) for t in terms if t and t.strip()]
        if not cleaned:
            return []
        pattern = re.compile("|".join(cleaned), re.IGNORECASE)
        return self._search_pattern(pattern, limit)

    def _search_pattern(self, pattern: re.Pattern[str], limit: int) -> list[EvidenceSnippet]:
        hits: list[EvidenceSnippet] = []
        for path in self._walk():
            if len(hits) >= limit:
                break
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > _MAX_BYTES_PER_FILE:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if pattern.search(line):
                    start = max(0, i - _CONTEXT_LINES)
                    end = min(len(lines), i + _CONTEXT_LINES + 1)
                    snippet_text = "\n".join(lines[start:end])
                    rel = path.relative_to(self.root)
                    hits.append(
                        EvidenceSnippet(
                            path=str(rel),
                            line_start=start + 1,
                            line_end=end,
                            text=snippet_text,
                            source=self.name,
                        )
                    )
                    if len(hits) >= limit:
                        break
        return hits

    def fetch(self, path: str, line_start: int = 0, line_end: int = 0) -> EvidenceSnippet:
        target = (self.root / path).resolve()
        # Refuse to read outside the repo root — a SourceAdapter must never
        # leak files the user didn't authorise. This is the trustworthiness
        # gate at the adapter boundary.
        try:
            target.relative_to(self.root)
        except ValueError:
            return EvidenceSnippet(path=path, line_start=0, line_end=0, text="", source=self.name)
        if not target.is_file():
            return EvidenceSnippet(path=path, line_start=0, line_end=0, text="", source=self.name)
        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return EvidenceSnippet(path=path, line_start=0, line_end=0, text="", source=self.name)
        if line_start <= 0:
            return EvidenceSnippet(
                path=path,
                line_start=1,
                line_end=len(lines),
                text="\n".join(lines),
                source=self.name,
            )
        start = max(1, line_start)
        end = max(start, line_end or start)
        return EvidenceSnippet(
            path=path,
            line_start=start,
            line_end=end,
            text="\n".join(lines[start - 1 : end]),
            source=self.name,
        )

    def find_readme(self) -> Path | None:
        for name in ("README.md", "README.rst", "README.txt", "readme.md", "README"):
            candidate = self.root / name
            if candidate.is_file():
                return candidate
        return None

    def _walk(self) -> Iterable[Path]:
        stack: list[Path] = [self.root]
        while stack:
            current = stack.pop()
            try:
                children = list(current.iterdir())
            except OSError:
                continue
            for child in children:
                if child.is_dir():
                    if child.name in self.exclude_dirs:
                        continue
                    stack.append(child)
                elif child.is_file():
                    if child.suffix.lower() in self.include_suffixes or child.name.lower() in {
                        "dockerfile",
                        "makefile",
                    }:
                        yield child
