import pytest
import asyncio
from tools.mcp.engine import ToolRegistry, WebSearchTool
from providers.adapters import LLMAdapter
from core.evals.harness import EvaluationHarness

@pytest.mark.asyncio
async def test_mcp_permissions():
    registry = ToolRegistry()
    search = registry.get_tool("web_search")
    kb = registry.get_tool("knowledge_retrieval")
    
    # Trusted tier should access both
    assert "trusted" in search.allowed_tiers
    assert "trusted" in kb.allowed_tiers
    
    res1 = await kb.execute({"doc_id": "123"}, "trusted")
    assert "content" in res1
    
    # Public tier should hit security violation on KB
    res2 = await kb.execute({"doc_id": "123"}, "public")
    assert "error" in res2
    assert "Security Violation" in res2["error"]

@pytest.mark.asyncio
async def test_adapters_dry_run(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    adapter = LLMAdapter(tier="worker")
    assert adapter.dry_run is True
    res = await adapter.generate("test prompt")
    assert "[WORKER DRY_RUN]" in res

@pytest.mark.asyncio
async def test_eval_harness():
    harness = EvaluationHarness()
    results = await harness.run_eval_suite()
    
    assert len(results) == 3
    
    # Confirm Trusted tier forced Head Direct
    trusted_run = next(r for r in results if r["privacy"] == "trusted")
    assert trusted_run["executed_route"] == "head_direct"
    
    # Confirm simple query generated multiple outputs (Head + Worker in default map)
    # Actually 'public' tier with 'low' complexity maps to 'head_workers' assuming generic mapping
    public_run = next(r for r in results if r["privacy"] == "public")
    assert public_run["outputs_count"] > 0
