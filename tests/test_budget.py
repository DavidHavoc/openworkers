"""Tests for the per-session hard budget ceiling.

Three layers:

1. **BudgetGuard mechanics** — estimate, check, reserve, record_actual.
2. **Context-var scoping** — concurrent guards don't share state.
3. **UnifiedLLM integration** — over-budget providers are skipped from the
   fallback chain; cheaper providers downstream still get a chance; when
   *every* provider's estimate is over budget the response surfaces the
   budget error in metadata.
"""

from __future__ import annotations

import asyncio

import pytest

from providers import budget as budget_module
from providers.budget import (
    BudgetExceededError,
    BudgetGuard,
    get_current_guard,
)

# ──────────────────────────────────────────────────────────────────────────
# guard mechanics
# ──────────────────────────────────────────────────────────────────────────


def test_guard_off_when_max_unset(monkeypatch):
    monkeypatch.delenv("MAX_BUDGET_USD", raising=False)
    g = BudgetGuard()
    assert g.enabled is False
    assert g.remaining() is None
    # No max → every call passes the cap regardless of estimate
    assert g.check(estimate_usd=99.0) is True


def test_guard_on_when_max_set_via_arg():
    g = BudgetGuard(max_usd=1.0)
    assert g.enabled is True
    assert g.remaining() == 1.0


def test_guard_on_when_max_set_via_env(monkeypatch):
    monkeypatch.setenv("MAX_BUDGET_USD", "2.50")
    g = BudgetGuard()
    assert g.enabled is True
    assert g.max_usd == 2.50


def test_guard_ignores_non_numeric_env(monkeypatch):
    monkeypatch.setenv("MAX_BUDGET_USD", "not-a-number")
    g = BudgetGuard()
    assert g.enabled is False


def test_estimate_grows_with_input_length():
    g = BudgetGuard(max_usd=10.0, output_token_floor=0)
    short = g.estimate("hi", "", "openai")
    long_input = g.estimate("x" * 1000, "", "openai")
    assert long_input > short


def test_estimate_uses_provider_rate():
    """Anthropic is more expensive per token than DeepSeek — so for the same prompt the estimate must be higher."""
    g = BudgetGuard(max_usd=10.0)
    e_anthropic = g.estimate("hello world", "system prompt", "anthropic")
    e_deepseek = g.estimate("hello world", "system prompt", "deepseek")
    assert e_anthropic > e_deepseek


def test_estimate_unknown_provider_uses_default_rate():
    g = BudgetGuard(max_usd=10.0)
    # Doesn't crash on an unrecognised provider; returns a positive number.
    assert g.estimate("hi", "", "totally_made_up_provider") > 0


def test_check_passes_when_estimate_fits():
    g = BudgetGuard(max_usd=1.0)
    assert g.check(0.5) is True


def test_check_fails_when_estimate_exceeds_remaining():
    g = BudgetGuard(max_usd=1.0)
    g.spent_usd = 0.9
    assert g.check(0.2) is False


def test_check_passes_at_exact_boundary():
    """Spent + estimate == max_usd is allowed; only strict overshoot fails."""
    g = BudgetGuard(max_usd=1.0)
    g.spent_usd = 0.4
    assert g.check(0.6) is True


def test_reserve_raises_when_over():
    g = BudgetGuard(max_usd=0.10)
    with pytest.raises(BudgetExceededError) as exc_info:
        g.reserve(0.20)
    msg = str(exc_info.value)
    assert "0.10" in msg or "0.100000" in msg


def test_record_actual_accumulates():
    g = BudgetGuard(max_usd=10.0)
    g.record_actual(0.40)
    g.record_actual(0.30)
    assert g.spent_usd == pytest.approx(0.70)
    assert g.remaining() == pytest.approx(9.30)


def test_record_actual_ignores_non_positive():
    g = BudgetGuard(max_usd=10.0)
    g.record_actual(0.0)
    g.record_actual(-1.0)
    assert g.spent_usd == 0.0


def test_reset_zeros_spend():
    g = BudgetGuard(max_usd=10.0)
    g.record_actual(2.5)
    g.reset()
    assert g.spent_usd == 0.0
    assert g.remaining() == 10.0


# ──────────────────────────────────────────────────────────────────────────
# contextvars scoping
# ──────────────────────────────────────────────────────────────────────────


def test_no_guard_outside_context():
    assert get_current_guard() is None


def test_guard_visible_inside_with_block():
    with BudgetGuard(max_usd=1.0) as g:
        assert get_current_guard() is g


def test_guard_uninstalled_after_with_block():
    with BudgetGuard(max_usd=1.0):
        pass
    assert get_current_guard() is None


def test_guards_nest():
    with BudgetGuard(max_usd=1.0) as outer:
        with BudgetGuard(max_usd=0.5) as inner:
            assert get_current_guard() is inner
        assert get_current_guard() is outer


@pytest.mark.asyncio
async def test_concurrent_guards_do_not_share_spend():
    """Two parallel sessions each track their own spend.

    Without contextvars the inner record_actual would mutate a shared
    counter and one session's spend would leak into the other.
    """

    async def session(spend_amount: float, max_usd: float) -> float:
        with BudgetGuard(max_usd=max_usd) as g:
            await asyncio.sleep(0)  # force interleaving
            g.record_actual(spend_amount)
            await asyncio.sleep(0)
            return g.spent_usd

    spends = await asyncio.gather(
        session(0.30, max_usd=1.0),
        session(0.70, max_usd=2.0),
    )
    assert sorted(spends) == pytest.approx([0.30, 0.70])


# ──────────────────────────────────────────────────────────────────────────
# UnifiedLLM integration
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unified_skips_provider_when_estimate_over_cap(monkeypatch):
    """A provider whose estimate exceeds remaining budget is skipped from the chain."""
    from providers.unified import UnifiedLLM

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("THESIS_BALANCED_PROVIDER", "anthropic")
    monkeypatch.setenv("THESIS_BALANCED_MODEL", "claude-sonnet-4-20250514")
    monkeypatch.delenv("MAX_BUDGET_USD", raising=False)

    llm = UnifiedLLM()
    llm.set_available_providers(["anthropic", "deepseek"])

    called = []

    async def fake_generate(provider, model, prompt, system_prompt, response_schema):
        called.append(provider)
        return f"output-from-{provider}"

    llm.set_generate_fn(fake_generate)

    # Cap is sized so anthropic's estimate (rate 0.015/1k tokens) won't
    # fit but deepseek's (0.0014/1k tokens) will. With prompt+system long
    # enough, anthropic's estimate clears the cap easily while deepseek
    # still squeaks in.
    long_prompt = "x" * 10000  # ~2857 input tokens + 500 floor → 3357 tokens
    # anthropic est: 3357/1000 * 0.015 ≈ $0.050; deepseek est ≈ $0.0047
    cap = 0.020  # blocks anthropic, allows deepseek

    with BudgetGuard(max_usd=cap):
        response = await llm.generate(prompt=long_prompt, mode="balanced")

    assert response.provider_used == "deepseek"
    assert response.fallback_used is True
    assert "anthropic" not in called, "anthropic must be skipped — estimate over cap"
    assert "deepseek" in called


@pytest.mark.asyncio
async def test_unified_returns_failure_when_all_providers_over_cap(monkeypatch):
    """If every provider's estimate exceeds the cap, all_providers_failed returns."""
    from providers.unified import UnifiedLLM

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("THESIS_BALANCED_PROVIDER", "anthropic")
    monkeypatch.setenv("THESIS_BALANCED_MODEL", "claude-sonnet-4-20250514")

    llm = UnifiedLLM()
    llm.set_available_providers(["anthropic", "openai", "deepseek"])

    called = []

    async def fake_generate(provider, model, prompt, system_prompt, response_schema):
        called.append(provider)
        return "should-not-run"

    llm.set_generate_fn(fake_generate)

    with BudgetGuard(max_usd=0.0000001):  # smaller than any non-zero estimate
        response = await llm.generate(prompt="hello", mode="balanced")

    assert response.provider_used == "none"
    assert response.fallback_used is True
    assert called == [], "no provider should have been called"
    assert response.metadata.get("last_error") is not None


@pytest.mark.asyncio
async def test_unified_records_actual_cost_into_guard(monkeypatch):
    """After a successful call, guard.spent_usd reflects the response's cost."""
    from providers.unified import UnifiedLLM

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("THESIS_BALANCED_PROVIDER", "deepseek")
    monkeypatch.setenv("THESIS_BALANCED_MODEL", "deepseek-chat")

    llm = UnifiedLLM()
    llm.set_available_providers(["deepseek"])

    async def fake_generate(provider, model, prompt, system_prompt, response_schema):
        return "x" * 700  # ~200 tokens of response

    llm.set_generate_fn(fake_generate)

    with BudgetGuard(max_usd=10.0) as guard:
        before = guard.spent_usd
        await llm.generate(prompt="hi", mode="balanced")
        after = guard.spent_usd

    assert after > before, "guard.spent_usd must grow after a successful call"


@pytest.mark.asyncio
async def test_unified_guard_off_means_no_skipping(monkeypatch):
    """When MAX_BUDGET_USD is unset, the cap doesn't affect routing."""
    from providers.unified import UnifiedLLM

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("THESIS_BALANCED_PROVIDER", "anthropic")
    monkeypatch.setenv("THESIS_BALANCED_MODEL", "claude-sonnet-4-20250514")
    monkeypatch.delenv("MAX_BUDGET_USD", raising=False)

    llm = UnifiedLLM()
    llm.set_available_providers(["anthropic"])

    called = []

    async def fake_generate(provider, model, prompt, system_prompt, response_schema):
        called.append(provider)
        return "ok"

    llm.set_generate_fn(fake_generate)

    # No `with BudgetGuard():` block — unconfigured.
    response = await llm.generate(prompt="x" * 10000, mode="balanced")
    assert response.provider_used == "anthropic"
    assert called == ["anthropic"]


# ──────────────────────────────────────────────────────────────────────────
# orchestrator wraps each session in a guard
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orchestrator_installs_guard_for_session(monkeypatch, tmp_path):
    """ThesisOrchestrator.execute() must run inside a BudgetGuard context."""
    from core.memory.episodic import EpisodicMemory
    from core.orchestrator.thesis_flow import ThesisOrchestrator
    from core.router.engine import Router
    from core.schemas import ResearchContext
    from providers.unified import UnifiedLLM
    from tools.mcp.engine import ToolRegistry

    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.chdir(tmp_path)

    saw_guard: list[BudgetGuard | None] = []

    real_head_execute = None  # populated below

    orch = ThesisOrchestrator(
        unified=UnifiedLLM(),
        memory=EpisodicMemory(qdrant_location=":memory:"),
        router=Router(),
        tool_registry=ToolRegistry(),
    )

    real_head_execute = orch.head.execute

    async def _capture_guard(task, ctx, mode="planner"):
        saw_guard.append(get_current_guard())
        return await real_head_execute(task, ctx, mode=mode)

    orch.head.execute = _capture_guard  # type: ignore[method-assign]

    rc = ResearchContext(
        research_question="Q",
        topic_summary="S",
        discipline="psychology",
    )
    await orch.execute(rc)

    assert any(
        g is not None for g in saw_guard
    ), "head.execute should run with a BudgetGuard installed in contextvars"


def test_module_singleton_resets():
    """Smoke test: module-level utilities don't leak state across tests."""
    assert budget_module.get_current_guard() is None


def test_unified_estimate_cost_includes_input_tokens_wr05():
    """WR-05 regression: _estimate_cost counts input tokens, not just response length.

    Pre-fix the estimate measured only the response string. For long prompts
    with short responses (the typical thesis-pipeline pattern) this systematically
    under-reported spend, letting cumulative cost drift past MAX_BUDGET_USD.
    """
    from providers.unified import COST_PER_1K_TOKENS, UnifiedLLM

    unified = UnifiedLLM()

    long_prompt = "x" * 10000
    long_system = "y" * 5000
    short_response = "ok"

    cost = unified._estimate_cost(short_response, "openai", long_prompt, long_system)

    # Expected: (10000 + 5000 + 2) / 3.5 ≈ 4286 tokens × $0.005/1K ≈ $0.0214
    expected_min = (15000 / 3.5 / 1000) * COST_PER_1K_TOKENS["openai"] * 0.95
    assert (
        cost >= expected_min
    ), f"Estimated cost {cost} is too low — input tokens may not be counted (WR-05)"

    # Sanity: response-only would be ~$0.0000029, far below expected_min.
    response_only = (len(short_response) / 3.5 / 1000) * COST_PER_1K_TOKENS["openai"]
    assert (
        cost > response_only * 100
    ), f"Cost {cost} is closer to response-only ({response_only}) than to input-inclusive"
