import re
import uuid
from typing import Any, List

from qdrant_client import QdrantClient

from core.embeddings import EMBEDDING_MODEL
from core.schemas import CorpusSection

SECTION_PATTERNS = [
    (r"(?i)^\s*(?:chapter\s+\d+[.:]\s*)?introduction\s*$", "introduction"),
    (
        r"(?i)^\s*(?:chapter\s+\d+[.:]\s*)?(?:literature\s+review|related\s+work|background)\s*$",
        "literature_review",
    ),
    (
        r"(?i)^\s*(?:chapter\s+\d+[.:]\s*)?(?:methodology|methods|experimental\s+setup|approach)\s*$",
        "methodology",
    ),
    (
        r"(?i)^\s*(?:chapter\s+\d+[.:]\s*)?(?:results|findings|experiments|evaluation)\s*$",
        "results",
    ),
    (r"(?i)^\s*(?:chapter\s+\d+[.:]\s*)?(?:discussion|analysis)\s*$", "discussion"),
    (r"(?i)^\s*(?:chapter\s+\d+[.:]\s*)?(?:conclusion|summary|future\s+work)\s*$", "conclusion"),
]

HEADING_RE = re.compile(
    r"(?m)^(?:(?:#{1,3}\s*)|(?:\d+(?:\.\d+)*\s+)|(?:CHAPTER\s+\d+\s*)|(?:[IVX]+\.\s*))(.+)$"
)

CITATION_RE = re.compile(
    r"\[\d+\]|\[\d+[,\s\-]+\d+\]|\(\w+\s+\d{4}\)|\(\d{4}\)|\\cite\{[^}]*\}|\\citep\{[^}]*\}"
)


def _detect_section_type(heading: str) -> str:
    h = heading.strip()
    for pattern, stype in SECTION_PATTERNS:
        if re.match(pattern, h):
            return stype
    lower = h.lower()
    if any(kw in lower for kw in ("intro", "background", "overview", "motivation", "context")):
        return "introduction"
    if any(
        kw in lower
        for kw in ("related", "literature", "prior", "previous work", "state of the art")
    ):
        return "literature_review"
    if any(
        kw in lower
        for kw in (
            "method",
            "approach",
            "design",
            "procedure",
            "protocol",
            "architecture",
            "framework",
            "setup",
        )
    ):
        return "methodology"
    if any(
        kw in lower
        for kw in ("experiment", "result", "evaluation", "finding", "performance", "outcome")
    ):
        return "results"
    if any(kw in lower for kw in ("discuss", "analysis", "implication", "limitation")):
        return "discussion"
    if any(kw in lower for kw in ("conclu", "summary", "future", "outlook")):
        return "conclusion"
    return "body"


def _count_citations(text: str) -> int:
    return len(CITATION_RE.findall(text))


def _split_sections(text: str) -> List[dict[str, Any]]:
    sections: List[dict[str, Any]] = []
    lines = text.split("\n")
    current_heading = "(Preamble)"
    current_lines: List[str] = []
    current_type = "introduction"

    for line in lines:
        m = HEADING_RE.match(line)
        if m:
            heading_text = m.group(1).strip()
            if len(heading_text) > 80:
                current_lines.append(line)
                continue
            if current_lines or sections:
                body = "\n".join(current_lines).strip()
                if len(body.split()) >= 3:
                    sections.append(
                        {
                            "heading": current_heading,
                            "section_type": current_type,
                            "text": body,
                            "word_count": len(body.split()),
                            "citation_count": _count_citations(body),
                        }
                    )
            current_heading = heading_text
            current_type = _detect_section_type(heading_text)
            current_lines = []
        else:
            current_lines.append(line)

    body = "\n".join(current_lines).strip()
    if len(body.split()) >= 3:
        sections.append(
            {
                "heading": current_heading,
                "section_type": current_type,
                "text": body,
                "word_count": len(body.split()),
                "citation_count": _count_citations(body),
            }
        )

    return sections


def _extract_text_from_pdf(filepath: str) -> str:
    try:
        import fitz

        doc = fitz.open(filepath)
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()
        return text
    except ImportError:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            return f.read()


class CorpusIngest:
    def __init__(self, path: str = "./qdrant_data"):
        from core.config import get_settings

        qdrant_url = get_settings().qdrant_url
        if qdrant_url:
            self.client = QdrantClient(url=qdrant_url)
        elif path == ":memory:":
            self.client = QdrantClient(location=":memory:")
        else:
            self.client = QdrantClient(path=path)

        self.collection_name = "thesis_corpus"
        self.client.set_model(EMBEDDING_MODEL)

        if not self.client.collection_exists(collection_name=self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=self.client.get_fastembed_vector_params(),
            )

    def ingest_pdf(
        self,
        filepath: str,
        title: str,
        discipline: str,
        university: str = "",
        year: int = 0,
    ) -> List[CorpusSection]:
        raw_text = _extract_text_from_pdf(filepath)
        return self._ingest_raw(raw_text, title, discipline, university, year)

    def ingest_text(
        self,
        text: str,
        title: str,
        discipline: str,
        university: str = "",
        year: int = 0,
    ) -> List[CorpusSection]:
        raw_text = str(text)
        return self._ingest_raw(raw_text, title, discipline, university, year)

    def _ingest_raw(
        self,
        text: str,
        title: str,
        discipline: str,
        university: str = "",
        year: int = 0,
    ) -> List[CorpusSection]:
        thesis_id = f"thesis-{uuid.uuid4().hex[:12]}"
        section_dicts = _split_sections(text)

        if not section_dicts:
            return []

        section_dicts = [sd for sd in section_dicts if sd["word_count"] >= 3]

        corpus_sections: List[CorpusSection] = []
        ids: List[str] = []
        documents: List[str] = []
        payloads: List[dict[str, Any]] = []

        for sd in section_dicts:
            section_id = str(uuid.uuid4())
            cs = CorpusSection(
                section_id=section_id,
                thesis_id=thesis_id,
                thesis_title=title,
                discipline=discipline,
                university=university,
                year=year,
                section_type=sd["section_type"],
                heading=sd["heading"],
                text=sd["text"],
                word_count=sd["word_count"],
                citation_count=sd["citation_count"],
            )
            corpus_sections.append(cs)
            ids.append(section_id)
            documents.append(sd["text"][:8000])
            payloads.append(cs.model_dump())

        try:
            self.client.add(
                collection_name=self.collection_name,
                documents=documents,
                metadata=payloads,
                ids=ids,
            )
        except Exception as e:
            import sys

            print(f"[CorpusIngest] add failed: {e}", file=sys.stderr)
            raise

        return corpus_sections
