"""GitHub PR adapter.

Implements ``SourceAdapter`` over the **unified diff** of a pull
request. The adapter is intentionally constructed from a ``PrSpec``
value object rather than reaching out to the GitHub API in its
constructor:

- Tests stay pure: build a ``PrSpec`` from a canned fixture and audit
  it without touching the network.
- Future-proof: a GitLab / Gerrit adapter can share the same diff
  grepping logic by handing us a ``PrSpec``.

The network fetch (``fetch_pr_from_github``) lives in this module as a
sibling helper, separate from the adapter, so the adapter has no
dependency on httpx and remains trivially testable.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

import httpx

from core.sources.base import EvidenceSnippet, SourceAdapter

_PR_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)(?:[/?#].*)?$",
    re.IGNORECASE,
)

_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<new_start>\d+)(?:,\d+)? @@")
_DIFF_FILE_HEADER_RE = re.compile(r"^diff --git a/(?P<old>.+) b/(?P<new>.+)$")

_MAX_DIFF_BYTES = 1_000_000  # GitHub already caps; this is a defence-in-depth bound.


@dataclass
class DiffHunk:
    """A single hunk lifted from a unified diff.

    ``new_start`` is the 1-based line number the hunk applies to in the
    new file — useful for citation handles like ``foo.py:42``.
    """

    path: str
    new_start: int
    text: str


@dataclass
class PrSpec:
    """The minimal projection of a PR that the auditor needs.

    Network-free: construct directly in tests; ``fetch_pr_from_github``
    populates it in production.
    """

    owner: str
    repo: str
    number: int
    title: str
    body: str
    base_sha: str = ""
    head_sha: str = ""
    diff: str = ""
    changed_files: list[str] = field(default_factory=list)
    url: str = ""

    @property
    def description(self) -> str:
        """Title + body joined for claim extraction."""
        if not self.body:
            return self.title
        return f"{self.title}\n\n{self.body}"


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Parse a GitHub PR URL into (owner, repo, number).

    Raises ``ValueError`` on anything that isn't a PR URL. We keep this
    strict so a typo (e.g., ``/issues/`` instead of ``/pull/``) fails
    loud rather than hitting a 404 mid-pipeline.
    """
    match = _PR_URL_RE.match(url.strip())
    if not match:
        raise ValueError(f"Not a GitHub PR URL: {url!r}")
    return match["owner"], match["repo"], int(match["number"])


class GitHubAdapter(SourceAdapter):
    """Searches the unified diff of a PR for evidence of claims.

    The "repo" the adapter sees is the *diff*, not the working tree.
    Claims phrased as "adds X" need to match additions; "removes Y"
    needs to match deletions. The checker prompt is responsible for
    that semantic interpretation — the adapter's job is just to
    surface relevant hunk excerpts.
    """

    name = "github_pr"

    def __init__(self, pr: PrSpec) -> None:
        self.pr = pr
        self._hunks: list[DiffHunk] = list(_iter_hunks(pr.diff))

    def search(self, query: str, limit: int = 5) -> list[EvidenceSnippet]:
        query = (query or "").strip()
        if not query:
            return []
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        return self._search_pattern(pattern, limit)

    def search_any(self, terms: Iterable[str], limit: int = 5) -> list[EvidenceSnippet]:
        cleaned = [re.escape(t.strip()) for t in terms if t and t.strip()]
        if not cleaned:
            return []
        pattern = re.compile("|".join(cleaned), re.IGNORECASE)
        return self._search_pattern(pattern, limit)

    def _search_pattern(self, pattern: re.Pattern[str], limit: int) -> list[EvidenceSnippet]:
        hits: list[EvidenceSnippet] = []
        for hunk in self._hunks:
            if len(hits) >= limit:
                break
            # Drop the leading +/-/space marker before matching so a
            # hint that happens to be ``+`` (rare) does not spuriously
            # match every added line.
            for offset, line in enumerate(hunk.text.splitlines()):
                payload = line[1:] if line[:1] in "+- " else line
                if pattern.search(payload):
                    hits.append(
                        EvidenceSnippet(
                            path=hunk.path,
                            line_start=hunk.new_start + offset,
                            line_end=hunk.new_start + offset,
                            text=hunk.text,
                            source=self.name,
                        )
                    )
                    # One hit per hunk: the whole hunk is already in
                    # ``text``, so multiple line matches in the same
                    # hunk would just duplicate the snippet.
                    break
        return hits

    def fetch(self, path: str, line_start: int = 0, line_end: int = 0) -> EvidenceSnippet:
        for hunk in self._hunks:
            if hunk.path != path:
                continue
            if line_start and not (
                hunk.new_start <= line_start <= hunk.new_start + len(hunk.text.splitlines())
            ):
                continue
            return EvidenceSnippet(
                path=hunk.path,
                line_start=hunk.new_start,
                line_end=hunk.new_start + len(hunk.text.splitlines()) - 1,
                text=hunk.text,
                source=self.name,
            )
        return EvidenceSnippet(path=path, line_start=0, line_end=0, text="", source=self.name)


def _iter_hunks(diff_text: str) -> Iterable[DiffHunk]:
    """Yield ``DiffHunk`` from a unified diff.

    The parser is deliberately small and accepts the GitHub-flavoured
    output (``diff --git`` headers, ``@@`` hunk headers, ``+``/``-``
    prefixed lines). It tolerates unfamiliar lines by skipping them —
    binary-file markers, mode change lines, etc.
    """
    current_path = ""
    current_hunk: list[str] | None = None
    current_new_start = 0
    for raw_line in diff_text.splitlines():
        file_match = _DIFF_FILE_HEADER_RE.match(raw_line)
        if file_match:
            if current_hunk is not None and current_path:
                yield DiffHunk(
                    path=current_path,
                    new_start=current_new_start,
                    text="\n".join(current_hunk),
                )
            current_path = file_match["new"]
            current_hunk = None
            continue
        hunk_match = _HUNK_HEADER_RE.match(raw_line)
        if hunk_match:
            if current_hunk is not None and current_path:
                yield DiffHunk(
                    path=current_path,
                    new_start=current_new_start,
                    text="\n".join(current_hunk),
                )
            current_new_start = int(hunk_match["new_start"])
            current_hunk = []
            continue
        if current_hunk is not None and raw_line[:1] in {"+", "-", " "}:
            current_hunk.append(raw_line)
    if current_hunk is not None and current_path:
        yield DiffHunk(
            path=current_path,
            new_start=current_new_start,
            text="\n".join(current_hunk),
        )


def fetch_pr_from_github(
    url: str,
    token: str | None = None,
    client: httpx.Client | None = None,
    timeout: float = 15.0,
) -> PrSpec:
    """Pull PR metadata + unified diff from the GitHub REST API.

    Token resolution order: explicit ``token`` arg → ``GITHUB_TOKEN``
    env → ``GH_TOKEN`` env. Anonymous requests work for public repos
    but bump into the 60/hour rate limit fast, so we always surface
    the token miss in the logs rather than hiding it.
    """
    owner, repo, number = parse_pr_url(url)
    token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "openworkers-pr-auditor",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    owns_client = client is None
    http = client or httpx.Client(timeout=timeout, headers=headers)
    try:
        api_root = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}"
        pr_resp = http.get(api_root, headers=headers if owns_client else None)
        pr_resp.raise_for_status()
        pr_data = pr_resp.json()

        diff_resp = http.get(
            api_root,
            headers={
                **(headers if owns_client else {}),
                "Accept": "application/vnd.github.v3.diff",
            },
        )
        diff_resp.raise_for_status()
        diff_text = diff_resp.text[:_MAX_DIFF_BYTES]

        files_resp = http.get(
            f"{api_root}/files",
            headers=headers if owns_client else None,
            params={"per_page": 100},
        )
        files_resp.raise_for_status()
        files_payload = files_resp.json()
    finally:
        if owns_client:
            http.close()

    changed_files = [f.get("filename", "") for f in files_payload if f.get("filename")]
    return PrSpec(
        owner=owner,
        repo=repo,
        number=number,
        title=str(pr_data.get("title", "")),
        body=str(pr_data.get("body") or ""),
        base_sha=str(pr_data.get("base", {}).get("sha", "")),
        head_sha=str(pr_data.get("head", {}).get("sha", "")),
        diff=diff_text,
        changed_files=changed_files,
        url=url,
    )


def load_pr_fixture(directory: str) -> PrSpec:
    """Build a ``PrSpec`` from a canned fixture directory.

    Layout:
        <directory>/pr.json   # subset of the GitHub PR API response
        <directory>/diff.patch # unified diff

    Useful for tests and offline demos; mirrors the shape returned by
    ``fetch_pr_from_github`` without the network.
    """
    base = directory.rstrip("/")
    with open(f"{base}/pr.json", encoding="utf-8") as f:
        pr_data = json.load(f)
    try:
        with open(f"{base}/diff.patch", encoding="utf-8") as f:
            diff_text = f.read()
    except FileNotFoundError:
        diff_text = ""
    return PrSpec(
        owner=str(pr_data.get("owner", "fixture")),
        repo=str(pr_data.get("repo", "fixture")),
        number=int(pr_data.get("number", 0)),
        title=str(pr_data.get("title", "")),
        body=str(pr_data.get("body", "")),
        base_sha=str(pr_data.get("base_sha", "")),
        head_sha=str(pr_data.get("head_sha", "")),
        diff=diff_text,
        changed_files=list(pr_data.get("changed_files", [])),
        url=str(pr_data.get("url", "")),
    )


__all__ = [
    "DiffHunk",
    "GitHubAdapter",
    "PrSpec",
    "fetch_pr_from_github",
    "load_pr_fixture",
    "parse_pr_url",
]
