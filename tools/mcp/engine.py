from abc import ABC, abstractmethod
from typing import Dict, Any, List
import time
import logging
import asyncio
from duckduckgo_search import DDGS

class MCPTool(ABC):
    """Base class for MCP-style tools."""
    name: str = "base_tool"
    description: str = "Base description"
    allowed_tiers: List[str] = ["public", "sanitized", "trusted"]
    timeout: int = 10  # seconds

    @abstractmethod
    def get_input_schema(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_output_schema(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def execute_impl(self, params: Dict[str, Any]) -> Dict[str, Any]:
        pass

    async def execute(self, params: Dict[str, Any], privacy_tier: str) -> Dict[str, Any]:
        """Wrapper to enforce permissions, timeouts (simulated), and audit logging."""
        if privacy_tier not in self.allowed_tiers:
            error_msg = f"Security Violation: Tier '{privacy_tier}' not allowed to access tool '{self.name}'."
            logging.warning(error_msg)
            return {"error": error_msg}

        start_time = time.time()
        logging.info(f"AUDIT: Executing tool '{self.name}' under tier '{privacy_tier}' with params: {params}")
        
        try:
            # In a real system, you'd wrap this in asyncio.wait_for(..., self.timeout)
            result = await self.execute_impl(params)
            duration = time.time() - start_time
            logging.info(f"AUDIT: Tool '{self.name}' completed in {duration:.2f}s")
            return result
        except Exception as e:
            logging.error(f"AUDIT: Tool '{self.name}' failed: {str(e)}")
            return {"error": str(e)}


class WebSearchTool(MCPTool):
    name = "web_search"
    description = "Searches the public web for current information."
    allowed_tiers = ["public", "sanitized", "trusted"]
    timeout = 15

    def get_input_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}

    def get_output_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"results": {"type": "array", "items": {"type": "string"}}}}

    async def execute_impl(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = params.get('query')
        if not query:
            return {"results": []}
            
        def _search():
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=5))
                
        try:
            results = await asyncio.to_thread(_search)
            formatted_results = [
                f"{r.get('title', '')} - {r.get('body', '')} ({r.get('href', '')})"
                for r in results
            ]
            return {"results": formatted_results}
        except Exception as e:
            return {"results": [f"Search failed: {str(e)}"]}


class KnowledgeRetrievalTool(MCPTool):
    name = "knowledge_retrieval"
    description = "Retrieves internal company knowledge base articles. Highly restricted."
    allowed_tiers = ["trusted"]
    timeout = 5

    def get_input_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"doc_id": {"type": "string"}}, "required": ["doc_id"]}

    def get_output_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"content": {"type": "string"}}}

    async def execute_impl(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"content": "Confidential internal architecture details..."}


class StructuredDataLookupTool(MCPTool):
    name = "structured_data_lookup"
    description = "Looks up database metrics or statistical data safely."
    allowed_tiers = ["sanitized", "trusted"]
    timeout = 10

    def get_input_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"metric": {"type": "string"}}, "required": ["metric"]}

    def get_output_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"value": {"type": "number"}}}

    async def execute_impl(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"value": 42.0}


class ToolRegistry:
    def __init__(self):
        from tools.mcp.academic import (
            ArxivSearchTool,
            SemanticScholarSearchTool,
            CrossRefVerificationTool,
        )
        self._tools: Dict[str, MCPTool] = {
            "web_search": WebSearchTool(),
            "knowledge_retrieval": KnowledgeRetrievalTool(),
            "structured_data": StructuredDataLookupTool(),
            "arxiv_search": ArxivSearchTool(),
            "semantic_scholar_search": SemanticScholarSearchTool(),
            "crossref_verification": CrossRefVerificationTool(),
        }

    def get_tool(self, tool_name: str) -> MCPTool:
        return self._tools.get(tool_name)
    
    def get_available_tools(self, privacy_tier: str) -> List[Dict[str, Any]]:
        available = []
        for name, tool in self._tools.items():
            if privacy_tier in tool.allowed_tiers:
                available.append({
                    "name": name,
                    "description": tool.description,
                    "schema": tool.get_input_schema()
                })
        return available
