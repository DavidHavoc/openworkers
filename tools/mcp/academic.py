import asyncio
import urllib.parse
import xml.etree.ElementTree as ET
import json
from typing import Dict, Any, List

import httpx

from tools.mcp.engine import MCPTool

ARXIV_NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0

_client: httpx.AsyncClient = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_keepalive_connections=6, max_connections=20),
            headers={"User-Agent": "OpenWorkers/1.0"},
        )
    return _client


async def _request_with_retry(
    method: str,
    url: str,
    *,
    headers: Dict[str, str] = None,
    max_retries: int = _MAX_RETRIES,
) -> httpx.Response:
    client = _get_client()
    last_exc: Exception = None
    for attempt in range(max_retries + 1):
        try:
            resp = await client.request(method, url, headers=headers)
            if resp.status_code in _RETRYABLE_STATUSES and attempt < max_retries:
                raise httpx.HTTPStatusError(
                    f"Retryable status {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
            resp.raise_for_status()
            return resp
        except (httpx.RequestError, httpx.HTTPStatusError) as e:
            last_exc = e
            if attempt < max_retries:
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                await asyncio.sleep(delay)
            else:
                raise last_exc
    raise last_exc


class ArxivSearchTool(MCPTool):
    name = "arxiv_search"
    description = "Queries the arXiv API and returns papers with verified arXiv IDs."
    allowed_tiers = ["public", "sanitized", "trusted"]
    timeout = 20

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 10},
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
                            "arxiv_id": {"type": "string"},
                            "title": {"type": "string"},
                            "authors": {"type": "array", "items": {"type": "string"}},
                            "year": {"type": "integer"},
                            "abstract": {"type": "string"},
                            "url": {"type": "string"},
                            "doi": {"type": "string"},
                            "categories": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "total_results": {"type": "integer"},
            },
        }

    async def execute_impl(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = params.get("query", "")
        max_results = min(params.get("max_results", 10), 50)

        if not query.strip():
            return {"papers": [], "total_results": 0, "error": "Empty query"}

        encoded_query = urllib.parse.quote_plus(query)
        url = (
            f"https://export.arxiv.org/api/query?"
            f"search_query=all:{encoded_query}&start=0&max_results={max_results}"
            f"&sortBy=relevance&sortOrder=descending"
        )

        try:
            resp = await _request_with_retry("GET", url)
            raw = resp.text
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300] if e.response.text else ""
            return {"papers": [], "total_results": 0, "error": f"arXiv API returned {e.response.status_code}: {body}"}
        except httpx.RequestError as e:
            return {"papers": [], "total_results": 0, "error": f"arXiv API request failed: {e}"}

        try:
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            return {"papers": [], "total_results": 0, "error": f"Failed to parse arXiv response: {str(e)}"}

        total_results_elem = root.find("opensearch:totalResults", ARXIV_NAMESPACES)
        total_results = int(total_results_elem.text) if total_results_elem is not None and total_results_elem.text else 0

        papers: List[Dict[str, Any]] = []
        for entry in root.findall("atom:entry", ARXIV_NAMESPACES):
            paper = self._parse_entry(entry)
            if paper:
                papers.append(paper)

        return {"papers": papers, "total_results": total_results}

    def _parse_entry(self, entry: ET.Element) -> Dict[str, Any]:
        def _text(tag: str, default: str = "") -> str:
            el = entry.find(f"atom:{tag}", ARXIV_NAMESPACES)
            return el.text.strip() if el is not None and el.text else default

        def _authors() -> List[str]:
            authors = []
            for author in entry.findall("atom:author", ARXIV_NAMESPACES):
                name_el = author.find("atom:name", ARXIV_NAMESPACES)
                if name_el is not None and name_el.text:
                    authors.append(name_el.text.strip())
            return authors

        arxiv_id_full = _text("id")
        arxiv_id = arxiv_id_full.split("/abs/")[-1] if "/abs/" in arxiv_id_full else arxiv_id_full.rsplit("/", 1)[-1]

        published = _text("published")
        year = 0
        if published:
            try:
                year = int(published[:4])
            except ValueError:
                pass

        categories = []
        primary_cat = entry.find("arxiv:primary_category", ARXIV_NAMESPACES)
        if primary_cat is not None:
            cat_term = primary_cat.get("term", "")
            if cat_term:
                categories.append(cat_term)
        for cat in entry.findall("atom:category", ARXIV_NAMESPACES):
            term = cat.get("term", "")
            if term and term not in categories:
                categories.append(term)

        doi_elements = entry.findall("arxiv:doi", ARXIV_NAMESPACES)
        doi = doi_elements[0].text.strip() if doi_elements and doi_elements[0].text else ""

        return {
            "arxiv_id": arxiv_id,
            "title": _text("title"),
            "authors": _authors(),
            "year": year,
            "abstract": _text("summary"),
            "url": arxiv_id_full,
            "doi": doi,
            "categories": categories,
        }


class SemanticScholarSearchTool(MCPTool):
    name = "semantic_scholar_search"
    description = "Queries the Semantic Scholar API and returns papers with DOIs and citation counts."
    allowed_tiers = ["public", "sanitized", "trusted"]
    timeout = 20

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
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
                            "authors": {"type": "array", "items": {"type": "string"}},
                            "year": {"type": "integer"},
                            "abstract": {"type": "string"},
                            "url": {"type": "string"},
                            "doi": {"type": "string"},
                            "citation_count": {"type": "integer"},
                            "source": {"type": "string"},
                        },
                    },
                },
                "total": {"type": "integer"},
            },
        }

    async def execute_impl(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = params.get("query", "")
        limit = min(params.get("limit", 10), 50)

        if not query.strip():
            return {"papers": [], "total": 0, "error": "Empty query"}

        fields = "title,authors,year,abstract,externalIds,url,citationCount"
        url = (
            f"https://api.semanticscholar.org/graph/v1/paper/search?"
            f"query={urllib.parse.quote_plus(query)}&limit={limit}&fields={fields}"
        )

        try:
            resp = await _request_with_retry("GET", url)
            data = resp.json()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300] if e.response.text else ""
            return {"papers": [], "total": 0, "error": f"Semantic Scholar API returned {e.response.status_code}: {body}"}
        except httpx.RequestError as e:
            return {"papers": [], "total": 0, "error": f"Semantic Scholar API request failed: {e}"}
        except json.JSONDecodeError:
            return {"papers": [], "total": 0, "error": "Semantic Scholar returned invalid JSON"}

        papers: List[Dict[str, Any]] = []
        for item in data.get("data", []):
            paper_id = item.get("paperId", "")
            ext_ids = item.get("externalIds", {}) or {}
            doi = ext_ids.get("DOI", "")
            title = item.get("title", "")
            if not title:
                continue

            url_value = item.get("url", "") or f"https://www.semanticscholar.org/paper/{paper_id}"
            papers.append({
                "paper_id": paper_id,
                "title": title,
                "authors": [a.get("name", "") for a in (item.get("authors") or [])],
                "year": item.get("year") or 0,
                "abstract": item.get("abstract", "") or "",
                "url": url_value,
                "doi": doi,
                "citation_count": item.get("citationCount", 0) or 0,
                "source": "semantic_scholar",
            })

        total = data.get("total", len(papers))
        return {"papers": papers, "total": total}


class CrossRefVerificationTool(MCPTool):
    name = "crossref_verification"
    description = "Verifies a DOI exists via the CrossRef API. Returns real metadata or {exists: false}."
    allowed_tiers = ["public", "sanitized", "trusted"]
    timeout = 15

    def get_input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "doi": {"type": "string"},
            },
            "required": ["doi"],
        }

    def get_output_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "exists": {"type": "boolean"},
                "title": {"type": "string"},
                "authors": {"type": "array", "items": {"type": "string"}},
                "year": {"type": "integer"},
                "publisher": {"type": "string"},
                "type": {"type": "string"},
                "url": {"type": "string"},
                "doi": {"type": "string"},
            },
        }

    async def execute_impl(self, params: Dict[str, Any]) -> Dict[str, Any]:
        doi = params.get("doi", "").strip()

        if not doi:
            return {"exists": False, "error": "No DOI provided"}

        url = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}"

        try:
            resp = await _request_with_retry("GET", url)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {"exists": False, "doi": doi}
            body = e.response.text[:300] if e.response.text else ""
            return {"exists": False, "doi": doi, "error": f"CrossRef API returned {e.response.status_code}: {body}"}
        except httpx.RequestError as e:
            return {"exists": False, "doi": doi, "error": f"CrossRef API request failed: {e}"}

        try:
            data = resp.json()
        except json.JSONDecodeError:
            return {"exists": False, "doi": doi, "error": "CrossRef returned invalid JSON"}

        message = data.get("message", {})
        if not message:
            return {"exists": False, "doi": doi}

        title_list = message.get("title", [])
        title = title_list[0] if title_list else ""

        authors_list: List[str] = []
        for a in message.get("author", []) or []:
            given = a.get("given", "")
            family = a.get("family", "")
            name = f"{given} {family}".strip()
            if name:
                authors_list.append(name)

        issued = message.get("issued", {})
        date_parts = issued.get("date-parts", [[0]])
        year = date_parts[0][0] if date_parts and date_parts[0] else 0

        return {
            "exists": True,
            "doi": message.get("DOI", doi),
            "title": title,
            "authors": authors_list,
            "year": year,
            "publisher": message.get("publisher", ""),
            "type": message.get("type", ""),
            "url": message.get("URL", ""),
        }
