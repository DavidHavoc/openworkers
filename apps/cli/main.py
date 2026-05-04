import argparse
import asyncio
import os
import sys
from datetime import datetime

from core.schemas import ResearchContext
from core.memory.episodic import EpisodicMemory
from core.router.engine import Router
from providers.adapters import create_unified_llm
from tools.mcp.engine import ToolRegistry
from core.orchestrator.thesis_flow import ThesisOrchestrator
from apps.shared.formatting import (
    format_session_text,
    format_critique_text,
    format_lit_map_text,
    format_citation_audit_text,
    format_as_json,
)


def _create_orchestrator() -> ThesisOrchestrator:
    unified = create_unified_llm()
    memory = EpisodicMemory(qdrant_location=":memory:")
    router = Router()
    tools = ToolRegistry()
    return ThesisOrchestrator(
        unified=unified,
        memory=memory,
        router=router,
        tool_registry=tools,
    )


def _output(result, fmt: str, output_file: str = None):
    text = format_as_json(result) if fmt == "json" else str(result)
    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Saved to {output_file}")
    else:
        print(text)


async def cmd_research(args):
    orch = _create_orchestrator()
    rc = ResearchContext(
        research_question=args.question,
        topic_summary=args.summary or args.question,
        discipline=args.discipline,
        existing_knowledge=args.knowledge or "",
        what_they_need=args.need or "",
    )
    session = await orch.execute(rc)
    if args.format == "json":
        _output(session, "json", args.output)
    else:
        text = format_session_text(session)
        _output(text, "text", args.output)
    return session


async def cmd_critique(args):
    orch = _create_orchestrator()
    rc = ResearchContext(
        research_question=args.text,
        topic_summary=args.text,
        discipline=args.discipline,
    )
    critique = await orch._critique_only(rc)
    if args.format == "json":
        _output(critique, "json", args.output)
    else:
        text = format_critique_text(critique)
        _output(text, "text", args.output)
    return critique


async def cmd_verify(args):
    orch = _create_orchestrator()
    result = await orch._verify_single_citation(
        claim=args.claim,
        doi_or_title=args.claim,
    )
    if args.format == "json":
        _output(result, "json", args.output)
    else:
        exists = result.get("exists", False)
        if exists:
            title = result.get("title", "Unknown")
            year = result.get("year", "?")
            publisher = result.get("publisher", "")
            authors = result.get("authors", [])
            print(f"DOI EXISTS: {title}")
            print(f"  Year: {year} | Publisher: {publisher}")
            if authors:
                print(f"  Authors: {', '.join(authors[:5])}")
        else:
            error = result.get("error", "")
            if error:
                print(f"DOI NOT FOUND: {error}")
            else:
                print("DOI NOT FOUND (does not exist)")
    return result


async def cmd_papers(args):
    orch = _create_orchestrator()
    papers = await orch._search_literature_raw(
        query=args.query,
        source=args.source,
        limit=args.limit,
    )
    if args.format == "json":
        _output({"papers": papers, "source": args.source, "query": args.query}, "json", args.output)
    else:
        print(f"Papers for: '{args.query}' (source={args.source}, limit={args.limit})")
        print(f"Found: {len(papers)}")
        if papers:
            for i, p in enumerate(papers):
                pid = p.get("paper_id") or p.get("arxiv_id") or p.get("doi", "?")
                title = p.get("title", "?")
                year = p.get("year", "?")
                print(f"  {i+1}. [{year}] {title[:80]}")
                print(f"     ID: {pid}")
    return papers


async def cmd_resume(args):
    print("Resume: loading session from Qdrant memory...")
    memory = EpisodicMemory(qdrant_location=":memory:")
    brief = memory.retrieve_guidance(args.session_id, task_type="thesis")
    print(f"Memory hits: {brief.similar_past_tasks_count}")
    print(f"Confidence: {brief.confidence}")
    print(f"Recommendation: {brief.recommended_routing_bias}")
    return brief


async def cmd_corpus(args):
    print(f"Corpus ingest: {args.path}")
    print(f"  Discipline: {args.discipline}")
    print(f"  (Phase 11 — not yet implemented)")
    return {"status": "not_implemented", "path": args.path, "discipline": args.discipline}


def add_output_args(p):
    p.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    p.add_argument("--output", type=str, default=None, help="Save output to file")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="thesis",
        description="Thesis Assistant CLI — research, critique, verify, and discover papers.",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_research = sub.add_parser("research", help="Full pipeline: search, classify, audit, critique")
    p_research.add_argument("question", type=str, help="Research question")
    p_research.add_argument("--summary", type=str, default="", help="Topic summary")
    p_research.add_argument("--discipline", type=str, default="general", help="e.g. computer_science")
    p_research.add_argument("--knowledge", type=str, default="", help="What you already know")
    p_research.add_argument("--need", type=str, default="", help="What you need help with")
    add_output_args(p_research)

    p_critique = sub.add_parser("critique", help="Critique an idea, claim, or draft section")
    p_critique.add_argument("text", type=str, help="Text to critique")
    p_critique.add_argument("--discipline", type=str, default="general")
    add_output_args(p_critique)

    p_verify = sub.add_parser("verify", help="Check if a citation is real and supports the claim")
    p_verify.add_argument("claim", type=str, help="Claim text or DOI")
    add_output_args(p_verify)

    p_papers = sub.add_parser("papers", help="Quick literature search, no LLM")
    p_papers.add_argument("query", type=str, help="Search query")
    p_papers.add_argument("--source", type=str, default="semantic_scholar", choices=["arxiv", "semantic_scholar"])
    p_papers.add_argument("--limit", type=int, default=10)
    add_output_args(p_papers)

    p_resume = sub.add_parser("resume", help="Reload a previous session from memory")
    p_resume.add_argument("session_id", type=str, help="Session ID to reload")
    add_output_args(p_resume)

    p_corpus = sub.add_parser("corpus", help="Add a thesis to the corpus")
    p_corpus.add_argument("path", type=str, help="Path to PDF or text file")
    p_corpus.add_argument("--discipline", type=str, default="general")
    add_output_args(p_corpus)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    command_map = {
        "research": cmd_research,
        "critique": cmd_critique,
        "verify": cmd_verify,
        "papers": cmd_papers,
        "resume": cmd_resume,
        "corpus": cmd_corpus,
    }

    handler = command_map.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    os.environ.setdefault("DRY_RUN", "true")

    try:
        result = asyncio.run(handler(args))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
