import os
from typing import Any, List

from qdrant_client import QdrantClient

from core.embeddings import EMBEDDING_MODEL
from core.schemas import CorpusSection, CorpusStats


class CorpusRetrieve:
    def __init__(self, path: str = "./qdrant_data"):
        qdrant_url = os.environ.get("QDRANT_URL")
        if qdrant_url:
            self.client = QdrantClient(url=qdrant_url)
        elif path == ":memory:":
            self.client = QdrantClient(location=":memory:")
        else:
            self.client = QdrantClient(path=path)

        self.collection_name = "thesis_corpus"
        self.client.set_model(EMBEDDING_MODEL)

    def query_similar_sections(
        self,
        query: str,
        discipline: str = "",
        section_type: str = "",
        top_k: int = 5,
    ) -> List[CorpusSection]:
        if not self.client.collection_exists(collection_name=self.collection_name):
            return []

        results = self.client.query(
            collection_name=self.collection_name,
            query_text=query,
            limit=top_k * 3 if (discipline or section_type) else top_k,
        )

        sections: List[CorpusSection] = []
        for r in results:
            meta = r.metadata or {}
            try:
                if "thesis_title" not in meta and "thesis_id" not in meta:
                    continue

                cs = CorpusSection.model_validate(meta)
                if discipline and cs.discipline.lower() != discipline.lower():
                    continue
                if section_type and cs.section_type != section_type:
                    continue
                sections.append(cs)
            except Exception:
                continue

        return sections[:top_k]

    def get_corpus_stats(self, discipline: str) -> CorpusStats:
        if not self.client.collection_exists(collection_name=self.collection_name):
            return CorpusStats(discipline=discipline)

        all_meta = self._scroll_all_metadata()

        stats = CorpusStats(discipline=discipline)
        thesis_ids: set[str] = set()
        section_stats: dict[str, Any] = {}

        for meta in all_meta:
            if meta.get("discipline", "").lower() != discipline.lower():
                continue
            thesis_ids.add(meta.get("thesis_id", ""))
            stype = meta.get("section_type", "body")
            wc = meta.get("word_count", 0)
            cc = meta.get("citation_count", 0)
            heading = meta.get("heading", "")

            if stype not in section_stats:
                section_stats[stype] = {
                    "section_count": 0,
                    "total_word_count": 0,
                    "total_citation_count": 0,
                    "headings": {},
                }

            section_stats[stype]["section_count"] += 1
            section_stats[stype]["total_word_count"] += wc
            section_stats[stype]["total_citation_count"] += cc
            if heading:
                section_stats[stype]["headings"][heading] = (
                    section_stats[stype]["headings"].get(heading, 0) + 1
                )

        stats.thesis_count = len(thesis_ids)

        for stype, data in section_stats.items():
            count = data["section_count"]
            thesis_count = max(stats.thesis_count, 1)
            common_headings = [h for h, n in data["headings"].items() if n / thesis_count > 0.5]
            stats.section_stats[stype] = {
                "section_count": count,
                "avg_word_count": round(data["total_word_count"] / max(count, 1)),
                "avg_citation_count": round(data["total_citation_count"] / max(count, 1)),
                "citation_density_per_1k": round(
                    (data["total_citation_count"] / max(data["total_word_count"], 1)) * 1000, 1
                ),
                "common_subsections": common_headings,
            }

        return stats

    def _scroll_all_metadata(self) -> List[dict[str, Any]]:
        all_meta: List[dict[str, Any]] = []
        offset = None
        while True:
            points, next_offset = self.client.scroll(
                collection_name=self.collection_name,
                limit=200,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for p in points:
                if p.payload:
                    all_meta.append(p.payload)
            if next_offset is None:
                break
            offset = next_offset
        return all_meta
