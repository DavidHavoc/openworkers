import asyncio
import json
import sys
import os
from typing import Dict, Any, Optional

from core.schemas import ResearchContext
from core.memory.episodic import EpisodicMemory
from core.router.engine import Router
from providers.adapters import create_unified_llm
from tools.mcp.engine import ToolRegistry
from core.orchestrator.thesis_flow import ThesisOrchestrator
from apps.shared.formatting import format_as_json


TOOL_DEFINITIONS = [
    {
        "name": "thesis_research",
        "description": "Run the full thesis assistant pipeline: literature search, classification, citation audit, and critique.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Research question"},
                "summary": {"type": "string", "description": "Topic summary (optional)"},
                "discipline": {"type": "string", "description": "Academic discipline", "default": "general"},
                "knowledge": {"type": "string", "description": "What you already know (optional)", "default": ""},
                "need": {"type": "string", "description": "What you need help with (optional)", "default": ""},
            },
            "required": ["question"],
        },
    },
    {
        "name": "thesis_critique",
        "description": "Critique an idea, claim, or draft section. Returns structured feedback: strengths, weaknesses, gaps, counterarguments, and suggestions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to critique"},
                "discipline": {"type": "string", "description": "Academic discipline", "default": "general"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "thesis_verify_citation",
        "description": "Check if a citation (DOI or paper title) is real via CrossRef API. Returns verified metadata or {exists: false}.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "claim": {"type": "string", "description": "DOI or paper identifier to verify"},
            },
            "required": ["claim"],
        },
    },
    {
        "name": "thesis_search_papers",
        "description": "Quick literature search on arXiv or Semantic Scholar. No LLM involved. Returns papers with verified IDs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "source": {"type": "string", "description": "arxiv or semantic_scholar", "enum": ["arxiv", "semantic_scholar"], "default": "semantic_scholar"},
                "limit": {"type": "integer", "description": "Max papers to return", "default": 10},
            },
            "required": ["query"],
        },
    },
]


class ThesisMCPServer:
    def __init__(self):
        self._orchestrator: Optional[ThesisOrchestrator] = None

    def _get_orchestrator(self) -> ThesisOrchestrator:
        if self._orchestrator is None:
            unified = create_unified_llm()
            memory = EpisodicMemory(qdrant_location=":memory:")
            router = Router()
            tools = ToolRegistry()
            self._orchestrator = ThesisOrchestrator(
                unified=unified,
                memory=memory,
                router=router,
                tool_registry=tools,
            )
        return self._orchestrator

    async def handle_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {}) or {}

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {
                        "name": "thesis-assistant",
                        "version": "0.1.0",
                    },
                    "capabilities": {
                        "tools": {},
                    },
                },
            }

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOL_DEFINITIONS},
            }

        if method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {}) or {}
            result = await self._call_tool(tool_name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {"type": "text", "text": result}
                    ]
                },
            }

        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}

    async def _call_tool(self, name: str, args: Dict[str, Any]) -> str:
        orch = self._get_orchestrator()

        try:
            if name == "thesis_research":
                rc = ResearchContext(
                    research_question=args.get("question", ""),
                    topic_summary=args.get("summary", args.get("question", "")),
                    discipline=args.get("discipline", "general"),
                    existing_knowledge=args.get("knowledge", ""),
                    what_they_need=args.get("need", ""),
                )
                session = await orch.execute(rc)
                return format_as_json(session)

            elif name == "thesis_critique":
                rc = ResearchContext(
                    research_question=args.get("text", ""),
                    topic_summary=args.get("text", ""),
                    discipline=args.get("discipline", "general"),
                )
                critique = await orch._critique_only(rc)
                return format_as_json(critique)

            elif name == "thesis_verify_citation":
                result = await orch._verify_single_citation(
                    claim=args.get("claim", ""),
                    doi_or_title=args.get("claim", ""),
                )
                return format_as_json(result)

            elif name == "thesis_search_papers":
                papers = await orch._search_literature_raw(
                    query=args.get("query", ""),
                    source=args.get("source", "semantic_scholar"),
                    limit=args.get("limit", 10),
                )
                return format_as_json({"papers": papers, "query": args.get("query", "")})

            else:
                return json.dumps({"error": f"Unknown tool: {name}"})

        except Exception as e:
            return json.dumps({"error": str(e)})

    def _send(self, message: Dict[str, Any]):
        sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    async def run(self):
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

        while True:
            try:
                line = await reader.readline()
                if not line:
                    break

                text = line.decode("utf-8").strip()
                if not text:
                    continue

                request = json.loads(text)
                response = await self.handle_request(request)

                if response is not None:
                    self._send(response)

            except json.JSONDecodeError:
                self._send({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                })
            except Exception as e:
                self._send({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32603, "message": str(e)},
                })
