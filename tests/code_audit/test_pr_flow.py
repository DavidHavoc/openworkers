"""End-to-end test for the PR auditor.

Same stub-LLM pattern as ``test_readme_flow.py``: we drive the full
flow (planner → diff-grep → checker + trust gate → critic) against a
canned PR fixture so we can assert verdict distribution deterministically.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.orchestrator.pr_flow import PrAuditOrchestrator
from core.schemas_audit import (
    VERDICT_CONTRADICTED,
    VERDICT_DRIFTED,
    VERDICT_UNSUPPORTED,
    VERDICT_VERIFIED,
)
from core.sources.github import (
    GitHubAdapter,
    PrSpec,
    load_pr_fixture,
    parse_pr_url,
)
from providers.unified import UnifiedLLM

FIXTURE_PR = Path(__file__).resolve().parent.parent / "fixtures" / "sample_pr"


_PLANNER_CLAIMS = {
    "readme_path": "https://github.com/example/widgetlib/pull/42",
    "claims": [
        {
            "claim_id": "claim-01",
            "claim_text": "adds a `widgetctl --port 9000` CLI for launching the dashboard",
            "claim_type": "add",
            "search_hints": ["widgetctl", "--port", "9000", "argparse"],
        },
        {
            "claim_id": "claim-02",
            "claim_text": "adds a new `Widget.render_html` method",
            "claim_type": "add",
            "search_hints": ["render_html", "Widget"],
        },
        {
            "claim_id": "claim-03",
            "claim_text": "adds tests covering the new render path",
            "claim_type": "test",
            "search_hints": ["test_render_html", "render_html"],
        },
        {
            "claim_id": "claim-04",
            "claim_text": "removes the deprecated `legacy_render` helper",
            "claim_type": "remove",
            "search_hints": ["legacy_render"],
        },
        {
            "claim_id": "claim-05",
            "claim_text": "introduces no telemetry",
            "claim_type": "behavior",
            "search_hints": ["telemetry", "emit_telemetry"],
        },
        {
            "claim_id": "claim-06",
            "claim_text": "Adds WIDGETLIB_DEBUG=1 flag for verbose logging",
            "claim_type": "add",
            "search_hints": ["WIDGETLIB_DEBUG"],
        },
        {
            "claim_id": "claim-07",
            "claim_text": "Performance: render is now 3x faster on the bench",
            "claim_type": "behavior",
            "search_hints": ["bench", "benchmark", "perf"],
        },
    ],
}


_CHECKER_VERDICTS = {
    "verdicts": [
        {
            "claim_id": "claim-01",
            "claim_text": _PLANNER_CLAIMS["claims"][0]["claim_text"],
            "verdict": VERDICT_DRIFTED,
            "confidence": 0.8,
            "evidence_paths": ["widgetlib/cli.py"],
            "notes": "PR claims --port 9000; diff adds --bind defaulting to 127.0.0.1:8000.",
        },
        {
            "claim_id": "claim-02",
            "claim_text": _PLANNER_CLAIMS["claims"][1]["claim_text"],
            "verdict": VERDICT_VERIFIED,
            "confidence": 0.95,
            "evidence_paths": ["widgetlib/widget.py"],
            "notes": "render_html method added on Widget.",
        },
        {
            "claim_id": "claim-03",
            "claim_text": _PLANNER_CLAIMS["claims"][2]["claim_text"],
            "verdict": VERDICT_VERIFIED,
            "confidence": 0.9,
            "evidence_paths": ["tests/test_render.py"],
            "notes": "Test file added asserting render_html output.",
        },
        {
            "claim_id": "claim-04",
            "claim_text": _PLANNER_CLAIMS["claims"][3]["claim_text"],
            "verdict": VERDICT_DRIFTED,
            "confidence": 0.7,
            "evidence_paths": ["widgetlib/cli.py"],
            "notes": "Diff touches cli.py near _legacy() but the helper is not removed.",
        },
        {
            "claim_id": "claim-05",
            "claim_text": _PLANNER_CLAIMS["claims"][4]["claim_text"],
            "verdict": VERDICT_CONTRADICTED,
            "confidence": 0.95,
            "evidence_paths": ["widgetlib/telemetry.py"],
            "notes": "Diff adds a telemetry emitter that posts to telemetry.example.com.",
        },
        # claim-06: LLM hallucinates verified — trust gate must override
        {
            "claim_id": "claim-06",
            "claim_text": _PLANNER_CLAIMS["claims"][5]["claim_text"],
            "verdict": VERDICT_VERIFIED,
            "confidence": 0.85,
            "evidence_paths": ["widgetlib/cli.py"],
            "notes": "Hallucinated by checker — trust gate must overwrite.",
        },
        {
            "claim_id": "claim-07",
            "claim_text": _PLANNER_CLAIMS["claims"][6]["claim_text"],
            "verdict": VERDICT_UNSUPPORTED,
            "confidence": 0.0,
            "evidence_paths": [],
            "notes": "No benchmark or measurement in the diff.",
        },
    ]
}


_CRITIC_RESPONSE = {
    "weak_verdicts": [],
    "missed_claims": [],
    "suggestions": ["Require benchmarks for performance claims in CI."],
    "overall_assessment": "Caught one drift in CLI flag, one contradicted no-telemetry, and two unsupported claims.",
}


def _make_stub_unified() -> UnifiedLLM:
    llm = UnifiedLLM()
    llm.dry_run = False
    llm.set_available_providers(["anthropic"])

    async def fake_generate(
        provider: str,
        model: str,
        prompt: str,
        system_prompt: str,
        response_schema: Any,
    ) -> str:
        if "PR PLANNER" in system_prompt:
            return json.dumps(_PLANNER_CLAIMS)
        if "PR CHECKER" in system_prompt:
            return json.dumps(_CHECKER_VERDICTS)
        if "AUDIT CRITIC" in system_prompt:
            return json.dumps(_CRITIC_RESPONSE)
        return "{}"

    llm.set_generate_fn(fake_generate)
    return llm


@pytest.fixture
def stubbed_unified(monkeypatch) -> UnifiedLLM:
    monkeypatch.setenv("DRY_RUN", "false")
    for tier in ("QUALITY", "BALANCED", "CHEAP"):
        monkeypatch.setenv(f"THESIS_{tier}_PROVIDER", "anthropic")
        monkeypatch.setenv(f"THESIS_{tier}_MODEL", "claude-sonnet-4-20250514")
    return _make_stub_unified()


def test_parse_pr_url_happy_path():
    owner, repo, number = parse_pr_url("https://github.com/example/widgetlib/pull/42")
    assert (owner, repo, number) == ("example", "widgetlib", 42)


def test_parse_pr_url_rejects_non_pr():
    with pytest.raises(ValueError):
        parse_pr_url("https://github.com/example/widgetlib/issues/42")


def test_load_pr_fixture_round_trips():
    pr = load_pr_fixture(str(FIXTURE_PR))
    assert pr.number == 42
    assert pr.title.startswith("feat:")
    assert "widgetctl" in pr.body
    assert pr.diff, "fixture diff should be non-empty"
    assert "telemetry.py" in pr.diff


def test_github_adapter_finds_hits_in_diff_only():
    pr = load_pr_fixture(str(FIXTURE_PR))
    adapter = GitHubAdapter(pr)
    # Real addition in the diff
    hits = adapter.search_any(["render_html"], limit=10)
    assert any(h.path == "widgetlib/widget.py" for h in hits)
    # Hallucinated env var: nowhere in the diff
    assert adapter.search_any(["WIDGETLIB_DEBUG"], limit=10) == []
    # Telemetry contradicts the "no telemetry" claim — should surface
    telemetry_hits = adapter.search_any(["telemetry", "emit_telemetry"], limit=10)
    assert any(h.path == "widgetlib/telemetry.py" for h in telemetry_hits)


@pytest.mark.asyncio
async def test_pr_audit_end_to_end(stubbed_unified):
    pr = load_pr_fixture(str(FIXTURE_PR))
    orch = PrAuditOrchestrator(unified=stubbed_unified)
    report, critique = await orch.audit(pr)

    by_id = {v.claim_id: v for v in report.verdicts}
    assert len(report.verdicts) == 7

    # Verified claims have evidence in the diff
    assert by_id["claim-02"].verdict == VERDICT_VERIFIED
    assert by_id["claim-02"].evidence_paths
    assert by_id["claim-03"].verdict == VERDICT_VERIFIED

    # Drifted: PR says --port, diff adds --bind
    assert by_id["claim-01"].verdict == VERDICT_DRIFTED

    # Contradicted: PR says "no telemetry" but diff adds it
    assert by_id["claim-05"].verdict == VERDICT_CONTRADICTED

    # Trust gate: hallucinated WIDGETLIB_DEBUG verdict overwritten
    assert by_id["claim-06"].verdict == VERDICT_UNSUPPORTED, (
        "Trust gate failed: stub hallucinated 'verified' for WIDGETLIB_DEBUG; "
        "trust gate should have overridden because the diff has zero hits."
    )
    assert by_id["claim-06"].evidence_paths == []
    assert by_id["claim-06"].confidence == 0.0

    # Unsupported: performance claim with no benchmark evidence
    assert by_id["claim-07"].verdict == VERDICT_UNSUPPORTED

    # Summary tallies
    assert sum(report.summary.values()) == len(report.verdicts)
    assert report.summary[VERDICT_VERIFIED] >= 2
    assert report.summary[VERDICT_UNSUPPORTED] >= 2

    # Target field carries the PR URL
    assert "pull/42" in report.target

    # Critic ran
    assert critique.suggestions


@pytest.mark.asyncio
async def test_pr_audit_empty_description(stubbed_unified):
    """An empty PR description must produce a structured no-op report."""
    pr = PrSpec(
        owner="example",
        repo="empty",
        number=1,
        title="",
        body="",
        diff="",
        changed_files=[],
        url="https://github.com/example/empty/pull/1",
    )
    orch = PrAuditOrchestrator(unified=stubbed_unified)
    report, critique = await orch.audit(pr)
    assert report.verdicts == []
    assert report.errors and "empty" in report.errors[0].lower()
    assert critique.weak_verdicts == []
