import argparse
import asyncio
import os
import sys

from apps.shared.formatting import (
    format_as_json,
    format_critique_text,
    format_session_text,
)
from core.memory.episodic import EpisodicMemory
from core.orchestrator.thesis_flow import ThesisOrchestrator
from core.router.engine import Router
from core.schemas import ResearchContext
from core.sessions.store import create_session_store
from providers.adapters import create_unified_llm
from tools.mcp.engine import ToolRegistry


def _create_orchestrator() -> ThesisOrchestrator:
    unified = create_unified_llm()
    memory = EpisodicMemory(qdrant_location=":memory:")
    router = Router()
    tools = ToolRegistry()
    store = create_session_store()
    return ThesisOrchestrator(
        unified=unified,
        memory=memory,
        router=router,
        tool_registry=tools,
        session_store=store,
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
        rag_collection=getattr(args, "rag_collection", None) or None,
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
    store = create_session_store()
    session = await store.load(args.session_id)
    if session is None:
        print(f"Session {args.session_id} not found.", file=sys.stderr)
        sys.exit(1)

    print(f"Resumed session: {session.session_id}")
    print(f"  Question: {session.research_context.research_question}")
    print(f"  Discipline: {session.research_context.discipline}")
    print(f"  Status: {session.status}")
    print(f"  Created: {session.created_at}")

    if args.format == "json":
        _output(session, "json", args.output)
    else:
        text = format_session_text(session)
        _output(text, "text", args.output)
    return session


async def cmd_sessions(args):
    store = create_session_store()
    sessions = await store.list_sessions(
        limit=args.limit,
        discipline=args.discipline,
        status=args.status,
    )
    count = await store.count()

    print(f"Past sessions ({count} total, showing {len(sessions)}):")
    for s in sessions:
        print(
            f"  {s['session_id'][:8]}...  {s.get('discipline', '?')}  "
            f"{s.get('status', '?')}  {s['created_at']}"
        )

    if args.format == "json":
        _output({"count": count, "sessions": sessions}, "json", args.output)
    return sessions


async def cmd_corpus(args):
    from core.corpus.ingest import CorpusIngest

    ingester = CorpusIngest()
    sections = ingester.ingest_pdf(
        filepath=args.path,
        title=args.title or os.path.basename(args.path),
        discipline=args.discipline,
        university=args.university or "",
        year=args.year or 0,
    )
    summary = {
        "status": "ingested",
        "path": args.path,
        "discipline": args.discipline,
        "sections": len(sections),
        "section_types": list(set(s.section_type for s in sections)),
    }
    if args.format == "json":
        _output(summary, "json", args.output)
    else:
        print(f"Ingested: {args.path}")
        print(f"  Discipline: {args.discipline}")
        print(f"  Sections: {len(sections)}")
        print(f"  Types: {summary['section_types']}")
    return summary


async def cmd_ingest(args):
    from tools.mcp.rag import RAGIndexer

    indexer = RAGIndexer()

    if args.ingest_action == "list":
        names = indexer.list_collections()
        if args.format == "json":
            _output({"collections": names}, "json", args.output)
        else:
            if not names:
                print("No RAG collections.")
            else:
                print("RAG collections:")
                for n in names:
                    print(f"  {n}")
        return names

    if args.ingest_action == "delete":
        deleted = indexer.delete_collection(args.collection)
        summary = {"collection": args.collection, "deleted": deleted}
        if args.format == "json":
            _output(summary, "json", args.output)
        else:
            print(f"{'Deleted' if deleted else 'Not found'}: {args.collection}")
        return summary

    # add
    chunks = indexer.ingest_file(
        path=args.path,
        collection=args.collection,
        title=args.title or os.path.basename(args.path),
        max_words=args.max_words,
        overlap_words=args.overlap,
    )
    summary = {
        "status": "ingested",
        "path": args.path,
        "collection": args.collection,
        "chunks": chunks,
    }
    if args.format == "json":
        _output(summary, "json", args.output)
    else:
        print(f"Ingested: {args.path}")
        print(f"  Collection: {args.collection}")
        print(f"  Chunks: {chunks}")
    return summary


def add_output_args(p):
    p.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    p.add_argument("--output", type=str, default=None, help="Save output to file")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="thesis",
        description="Thesis Assistant CLI  -  research, critique, verify, and discover papers.",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_research = sub.add_parser("research", help="Full pipeline: search, classify, audit, critique")
    p_research.add_argument("question", type=str, help="Research question")
    p_research.add_argument("--summary", type=str, default="", help="Topic summary")
    p_research.add_argument(
        "--discipline", type=str, default="general", help="e.g. computer_science"
    )
    p_research.add_argument("--knowledge", type=str, default="", help="What you already know")
    p_research.add_argument("--need", type=str, default="", help="What you need help with")
    p_research.add_argument(
        "--rag-collection",
        dest="rag_collection",
        type=str,
        default=None,
        help="Pull additional context from a user RAG collection (see `thesis ingest`)",
    )
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
    p_papers.add_argument(
        "--source", type=str, default="semantic_scholar", choices=["arxiv", "semantic_scholar"]
    )
    p_papers.add_argument("--limit", type=int, default=10)
    add_output_args(p_papers)

    p_resume = sub.add_parser("resume", help="Reload a previous session from memory")
    p_resume.add_argument("session_id", type=str, help="Session ID to reload")
    add_output_args(p_resume)

    p_sessions = sub.add_parser("sessions", help="List past research sessions")
    p_sessions.add_argument("--limit", type=int, default=20, help="Max sessions to show")
    p_sessions.add_argument("--discipline", type=str, default=None, help="Filter by discipline")
    p_sessions.add_argument(
        "--status", type=str, default=None, choices=["complete", "partial"], help="Filter by status"
    )
    add_output_args(p_sessions)

    p_corpus = sub.add_parser("corpus", help="Add a thesis to the corpus")
    p_corpus.add_argument("path", type=str, help="Path to PDF or text file")
    p_corpus.add_argument("--discipline", type=str, default="general")
    p_corpus.add_argument("--title", type=str, default="", help="Thesis title")
    p_corpus.add_argument("--university", type=str, default="", help="University")
    p_corpus.add_argument("--year", type=int, default=0, help="Year of publication")
    add_output_args(p_corpus)

    p_ingest = sub.add_parser(
        "ingest", help="Manage user RAG collections (PDF/text -> Qdrant)"
    )
    ingest_sub = p_ingest.add_subparsers(dest="ingest_action", required=True)

    p_ingest_add = ingest_sub.add_parser("add", help="Add a PDF or text file to a RAG collection")
    p_ingest_add.add_argument("path", type=str, help="Path to PDF, .txt, or .md file")
    p_ingest_add.add_argument(
        "--collection", "-c", type=str, default="default", help="Collection name"
    )
    p_ingest_add.add_argument("--title", type=str, default="", help="Display label for the source")
    p_ingest_add.add_argument(
        "--max-words", dest="max_words", type=int, default=300, help="Max words per chunk"
    )
    p_ingest_add.add_argument(
        "--overlap", type=int, default=50, help="Word overlap between adjacent chunks"
    )
    add_output_args(p_ingest_add)

    p_ingest_list = ingest_sub.add_parser("list", help="List user RAG collections")
    add_output_args(p_ingest_list)

    p_ingest_delete = ingest_sub.add_parser("delete", help="Delete a user RAG collection")
    p_ingest_delete.add_argument("collection", type=str, help="Collection name")
    add_output_args(p_ingest_delete)

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
        "sessions": cmd_sessions,
        "corpus": cmd_corpus,
        "ingest": cmd_ingest,
    }

    handler = command_map.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    os.environ.setdefault("DRY_RUN", "true")

    try:
        _ = asyncio.run(handler(args))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
