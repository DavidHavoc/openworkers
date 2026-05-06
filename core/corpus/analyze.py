from typing import Dict, Any, Optional

from core.schemas import CorpusStats, CorpusSection, CorpusContext
from core.corpus.retrieve import CorpusRetrieve


class CorpusAnalyzer:
    def __init__(self, retriever: CorpusRetrieve = None):
        self.retriever = retriever or CorpusRetrieve()

    def analyze(self,
                query: str,
                discipline: str,
                section_type: str = "",
                student_word_count: int = 0,
                top_k: int = 5,
                ) -> CorpusContext:
        similar = self.retriever.query_similar_sections(
            query=query,
            discipline=discipline,
            section_type=section_type,
            top_k=top_k,
        )

        benchmarks: Optional[CorpusStats] = None
        try:
            benchmarks = self.retriever.get_corpus_stats(discipline)
        except Exception:
            pass

        return CorpusContext(
            similar_sections=similar,
            benchmarks=benchmarks,
        )

    def format_benchmarks_for_prompt(self, context: CorpusContext, student_wc: int = 0, section_type: str = "") -> str:
        lines = ["\n## CORPUS BENCHMARKS (from successful theses)"]

        if context.benchmarks and context.benchmarks.section_stats:
            bs = context.benchmarks
            lines.append(f"Corpus: {bs.discipline} ({bs.thesis_count} theses)")
            for stype, stats in sorted(bs.section_stats.items()):
                avg_wc = stats.get("avg_word_count", 0)
                avg_cc = stats.get("avg_citation_count", 0)
                density = stats.get("citation_density_per_1k", 0)
                subsections = stats.get("common_subsections", [])
                lines.append(
                    f"  {stype}: avg {avg_wc} words, "
                    f"{avg_cc} citations ({density}/1k words)"
                )
                if subsections:
                    lines.append(f"    Common subsections: {', '.join(subsections[:5])}")

            if student_wc > 0 and section_type and section_type in bs.section_stats:
                avg = bs.section_stats[section_type].get("avg_word_count", 0)
                ratio = f"{student_wc}/{avg}" if avg else str(student_wc)
                lines.append(
                    f"\nYour {section_type}: {ratio} words "
                    f"(corpus avg: {avg} words)"
                )

        if context.similar_sections:
            lines.append("\n## SIMILAR THESIS SECTIONS")
            for i, s in enumerate(context.similar_sections, 1):
                lines.append(
                    f"  {i}. [{s.year}] {s.thesis_title[:60]}  -  {s.section_type}"
                )
                lines.append(f"     {s.word_count} words, {s.citation_count} citations")
                lines.append(f"     Excerpt: {s.text[:120].replace(chr(10), ' ')}...")
                lines.append("")

        return "\n".join(lines)
