"""SourceAdapter layer: pluggable evidence retrieval for audit pipelines.

Each adapter implements a uniform contract (search, fetch, cite) so domain
flows can compose evidence from heterogeneous sources without hardcoding
which backend they speak to. The literature-domain tools under
``tools/mcp/`` will migrate behind this contract in a later slice; for the
README-audit slice we ship only the local-repo adapter.
"""

from core.sources.base import EvidenceSnippet, SourceAdapter
from core.sources.github import (
    GitHubAdapter,
    PrSpec,
    fetch_pr_from_github,
    load_pr_fixture,
    parse_pr_url,
)
from core.sources.local_repo import LocalRepoAdapter

__all__ = [
    "EvidenceSnippet",
    "GitHubAdapter",
    "LocalRepoAdapter",
    "PrSpec",
    "SourceAdapter",
    "fetch_pr_from_github",
    "load_pr_fixture",
    "parse_pr_url",
]
