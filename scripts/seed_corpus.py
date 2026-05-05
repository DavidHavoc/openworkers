#!/usr/bin/env python3
"""Seed the thesis corpus with 10 open-access theses/papers from arXiv.

Downloads PDFs via arXiv, extracts text, splits into sections, embeds, and stores in Qdrant.
Run: python -m scripts.seed_corpus
"""

import os
import json
import urllib.request
import urllib.parse
import tempfile
import xml.etree.ElementTree as ET
from typing import List, Dict, Any

ARXIV_NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}

SEED_QUERIES: Dict[str, str] = {
    "computer_science": "deep learning neural network architecture",
    "psychology": "social media adolescent mental health",
    "biology": "CRISPR gene editing applications",
    "physics": "quantum computing error correction",
    "economics": "machine learning economic forecasting",
}


def _search_arxiv(query: str, max_results: int = 3) -> List[Dict[str, Any]]:
    url = (
        "https://export.arxiv.org/api/query?"
        f"search_query=all:{urllib.parse.quote_plus(query)}"
        f"&start=0&max_results={max_results}&sortBy=relevance&sortOrder=descending"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "SeedCorpus/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
        root = ET.fromstring(raw)
        papers = []
        for entry in root.findall("atom:entry", ARXIV_NAMESPACES):
            title_el = entry.find("atom:title", ARXIV_NAMESPACES)
            summary_el = entry.find("atom:summary", ARXIV_NAMESPACES)
            id_el = entry.find("atom:id", ARXIV_NAMESPACES)
            published_el = entry.find("atom:published", ARXIV_NAMESPACES)

            title = title_el.text.strip() if title_el is not None and title_el.text else "Unknown"
            abstract = summary_el.text.strip() if summary_el is not None and summary_el.text else ""
            arxiv_id = id_el.text.strip().split("/abs/")[-1] if id_el is not None and id_el.text else "unknown"
            year = 0
            if published_el is not None and published_el.text:
                try:
                    year = int(published_el.text[:4])
                except ValueError:
                    pass

            authors = []
            for author_el in entry.findall("atom:author", ARXIV_NAMESPACES):
                name_el = author_el.find("atom:name", ARXIV_NAMESPACES)
                if name_el is not None and name_el.text:
                    authors.append(name_el.text.strip())

            papers.append({
                "arxiv_id": arxiv_id,
                "title": title,
                "abstract": abstract,
                "year": year,
                "authors": authors,
            })
        return papers
    except Exception as e:
        print(f"    Search error: {e}")
        return []


def _download_pdf_text(arxiv_id: str) -> str:
    url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    req = urllib.request.Request(url, headers={"User-Agent": "SeedCorpus/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            pdf_bytes = resp.read()
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()
        return text
    except Exception as e:
        print(f"      PDF download failed: {e}")
        return ""


def seed_corpus(dry_run: bool = True):
    if dry_run:
        print("[DRY_RUN] Would seed corpus with 10 open-access theses.")
        return

    from core.corpus.ingest import CorpusIngest

    ingester = CorpusIngest()
    total_sections = 0

    for discipline, query in SEED_QUERIES.items():
        print(f"Discipline: {discipline} — searching: '{query}'")
        papers = _search_arxiv(query, max_results=2)

        for paper in papers:
            title = paper["title"]
            arxiv_id = paper["arxiv_id"]
            year = paper["year"]
            print(f"  [{arxiv_id}] {title[:80]} ({year})")

            abstract = paper.get("abstract", "")
            if abstract:
                full_text = f"# {title}\n\n## Authors\n{', '.join(paper.get('authors', []))}\n\n## Abstract\n{abstract}"
                pdf_text = _download_pdf_text(arxiv_id)
                if pdf_text:
                    full_text += f"\n\n{pdf_text}"

                try:
                    sections = ingester.ingest_text(
                        text=full_text,
                        title=title,
                        discipline=discipline,
                        university="arXiv",
                        year=year,
                    )
                    total_sections += len(sections)
                    print(f"    Ingested {len(sections)} sections")
                except Exception as e:
                    print(f"    Ingest error: {e}")

    print(f"\nSeeded {total_sections} total sections across {len(SEED_QUERIES)} disciplines.")


if __name__ == "__main__":
    os.environ.setdefault("DRY_RUN", "true")
    seed_corpus(dry_run=os.environ.get("DRY_RUN", "true").lower() == "true")
