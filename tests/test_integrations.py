import asyncio
import json

import fakeredis
import httpx
import pytest

from core.evals.harness import EvaluationHarness
from core.schemas import CitationAudit, CritiqueResult, LitMap, ResearchPlan, SynthesisReport
from providers.adapters import LLMAdapter
from providers.placeholders import generate_placeholder_json
from tools.mcp.engine import ToolRegistry


@pytest.fixture(autouse=True)
def mock_redis(monkeypatch):
    server = fakeredis.FakeServer()
    monkeypatch.setattr(
        "redis.from_url",
        lambda *args, **kwargs: fakeredis.FakeRedis(server=server, decode_responses=True),
    )


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
    adapter = LLMAdapter(provider="deepseek")
    assert adapter.dry_run is True
    res = await adapter.generate("test prompt")
    assert "DEEPSEEK" in res
    assert "DRY_RUN" in res


def test_placeholder_json_validates_pydantic_models():
    """Placeholder JSON from generate_placeholder_json passes Pydantic model_validate_json."""
    for model_cls in (ResearchPlan, LitMap, CitationAudit, SynthesisReport, CritiqueResult):
        schema = model_cls.model_json_schema()
        schema.pop("title", None)
        raw = generate_placeholder_json(schema)
        parsed = json.loads(raw)
        assert isinstance(parsed, dict), f"{model_cls.__name__} placeholder is not a JSON object"
        instance = model_cls.model_validate_json(raw)
        assert isinstance(instance, model_cls), f"{model_cls.__name__} validation failed"


@pytest.mark.parametrize("provider", ["anthropic", "openai", "deepseek"])
@pytest.mark.asyncio
async def test_structured_output_dry_run_per_provider(monkeypatch, provider):
    """Each provider's adapter returns valid JSON when response_schema is given in dry_run."""
    monkeypatch.setenv("DRY_RUN", "true")
    adapter = LLMAdapter(provider=provider)
    schema = ResearchPlan.model_json_schema()
    schema.pop("title", None)

    result = await adapter.generate("test", response_schema=schema)
    parsed = json.loads(result)
    assert isinstance(parsed, dict)
    instance = ResearchPlan.model_validate_json(result)
    assert isinstance(instance, ResearchPlan)


@pytest.mark.asyncio
async def test_structured_output_unified_dry_run(monkeypatch):
    """UnifiedLLM.generate() with response_schema returns valid JSON in dry_run."""
    from providers.unified import UnifiedLLM

    monkeypatch.setenv("DRY_RUN", "true")
    unified = UnifiedLLM()
    unified.set_available_providers(["anthropic", "openai", "deepseek"])

    schema = CritiqueResult.model_json_schema()
    schema.pop("title", None)

    resp = await unified.generate(
        prompt="test",
        mode="quality",
        response_schema=schema,
    )
    assert resp.dry_run is True
    parsed = json.loads(resp.content)
    assert isinstance(parsed, dict)
    instance = CritiqueResult.model_validate_json(resp.content)
    assert isinstance(instance, CritiqueResult)


def test_structured_output_schema_includes_all_required_fields():
    """Generated placeholder JSON includes all required fields from the schema."""
    schema = CitationAudit.model_json_schema()
    schema.pop("title", None)
    required = schema.get("required", [])

    raw = json.loads(generate_placeholder_json(schema))
    for key in required:
        assert key in raw, f"Required key '{key}' missing from placeholder for CitationAudit"

    schema2 = LitMap.model_json_schema()
    schema2.pop("title", None)
    raw2 = json.loads(generate_placeholder_json(schema2))
    for key in schema2.get("required", []):
        assert key in raw2, f"Required key '{key}' missing from placeholder for LitMap"


def test_structured_output_schema_idempotent_cache():
    """_schema_for caches schemas and returns the same object on repeated calls."""
    from providers.thesis_agents import _schema_for

    s1 = _schema_for(ResearchPlan)
    s2 = _schema_for(ResearchPlan)
    assert s1 is s2


@pytest.mark.asyncio
async def test_request_with_retry_success_first_attempt(monkeypatch):
    """_request_with_retry returns the response on the first successful attempt."""
    from tools.mcp.academic import _request_with_retry

    resp = await _request_with_retry("GET", "https://httpbin.org/get", max_retries=0)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_request_with_retry_on_5xx(monkeypatch):
    """_request_with_retry retries on 503 and succeeds on the next attempt."""
    import httpx

    from tools.mcp import academic

    async def _fake_sleep(_delay):
        pass

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    call_count = [0]

    async def _mock_request(client, method, url, headers=None, extensions=None):
        call_count[0] += 1
        if call_count[0] < 2:
            req = httpx.Request(method, url)
            resp = httpx.Response(503, request=req)
            raise httpx.HTTPStatusError("Service Unavailable", request=req, response=resp)
        req = httpx.Request(method, url)
        return httpx.Response(200, request=req, json={"ok": True})

    monkeypatch.setattr(httpx.AsyncClient, "request", _mock_request)
    academic._client = None

    resp = await academic._request_with_retry("GET", "https://example.com", max_retries=2)
    assert resp.status_code == 200
    assert call_count[0] == 2


@pytest.mark.asyncio
async def test_request_with_retry_timeout_retries(monkeypatch):
    """_request_with_retry retries on RequestError (timeout) and succeeds."""
    import httpx

    from tools.mcp import academic

    async def _fake_sleep(_delay):
        pass

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    call_count = [0]

    async def _mock_request(client, method, url, headers=None, extensions=None):
        call_count[0] += 1
        if call_count[0] < 3:
            raise httpx.ReadTimeout("read timeout")
        req = httpx.Request(method, url)
        return httpx.Response(200, request=req, json={"ok": True})

    monkeypatch.setattr(httpx.AsyncClient, "request", _mock_request)
    academic._client = None

    resp = await academic._request_with_retry("GET", "https://example.com", max_retries=3)
    assert resp.status_code == 200
    assert call_count[0] == 3


@pytest.mark.asyncio
async def test_request_with_retry_max_retries_exceeded(monkeypatch):
    """_request_with_retry raises after exhausting all retries."""
    import httpx

    from tools.mcp import academic

    async def _fake_sleep(_delay):
        pass

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    async def _mock_request(client, method, url, headers=None, extensions=None):
        raise httpx.ConnectTimeout("connect timeout")

    monkeypatch.setattr(httpx.AsyncClient, "request", _mock_request)
    academic._client = None

    with pytest.raises(httpx.RequestError):
        await academic._request_with_retry("GET", "https://example.com", max_retries=2)


def test_http_client_uses_connection_pooling():
    """_get_client returns the same AsyncClient instance (connection pooling)."""
    import tools.mcp.academic as academic
    from tools.mcp.academic import _get_client

    academic._client = None
    c1 = _get_client()
    c2 = _get_client()
    assert c1 is c2
    assert isinstance(c1.timeout, httpx.Timeout)


def test_timeout_configuration():
    """The shared httpx client has connect=5s, read=30s, write=10s configured."""
    import tools.mcp.academic as academic
    from tools.mcp.academic import _get_client

    academic._client = None
    client = _get_client()
    timeout = client.timeout
    assert timeout.connect == 5.0
    assert timeout.read == 30.0
    assert timeout.write == 10.0


def test_output_schemas_preserved():
    """Academic tool output schemas remain unchanged after migration."""
    from tools.mcp.academic import (
        ArxivSearchTool,
        CrossRefVerificationTool,
        SemanticScholarSearchTool,
    )

    arxiv = ArxivSearchTool()
    schema = arxiv.get_output_schema()
    assert "papers" in schema["properties"]
    assert "total_results" in schema["properties"]

    s2 = SemanticScholarSearchTool()
    schema2 = s2.get_output_schema()
    assert "papers" in schema2["properties"]
    assert "total" in schema2["properties"]

    cross = CrossRefVerificationTool()
    schema3 = cross.get_output_schema()
    assert "exists" in schema3["properties"]
    assert "doi" in schema3["properties"]


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
