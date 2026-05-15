"""End-to-end test for the README auditor.

Uses a stubbed ``UnifiedLLM.generate_fn`` rather than DRY_RUN so we can
exercise the full flow (planner → researcher → checker → critic) with
deterministic LLM responses. The DRY_RUN placeholder generator returns
empty arrays for any list field, which would leave us with zero claims
and an empty audit — not a useful regression target.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.orchestrator.readme_flow import ReadmeAuditOrchestrator
from core.schemas_audit import (
    VERDICT_CONTRADICTED,
    VERDICT_DRIFTED,
    VERDICT_UNSUPPORTED,
    VERDICT_VERIFIED,
)
from core.sources.local_repo import LocalRepoAdapter
from providers.unified import UnifiedLLM

FIXTURE_REPO = Path(__file__).resolve().parent.parent / "fixtures" / "sample_repo"


_PLANNER_CLAIMS = {
    "claims": [
        {
            "claim_id": "claim-01",
            "claim_text": "Install via `pip install widgetlib==1.2.0`.",
            "claim_type": "install",
            "search_hints": ["widgetlib", "1.2.0", "version"],
        },
        {
            "claim_id": "claim-02",
            "claim_text": "Import `Widget` from `widgetlib` and call `render()` to produce HTML.",
            "claim_type": "usage",
            "search_hints": ["Widget", "render", "widgetlib"],
        },
        {
            "claim_id": "claim-03",
            "claim_text": "Set `WIDGETLIB_DEBUG=1` to enable verbose logging.",
            "claim_type": "feature",
            "search_hints": ["WIDGETLIB_DEBUG"],
        },
        {
            "claim_id": "claim-04",
            "claim_text": "Run `widgetctl --port 9000` to start the dashboard.",
            "claim_type": "usage",
            "search_hints": ["widgetctl", "--port"],
        },
        {
            "claim_id": "claim-05",
            "claim_text": "The render pipeline ships with zero dependencies.",
            "claim_type": "feature",
            "search_hints": ["dependencies"],
        },
        {
            "claim_id": "claim-06",
            "claim_text": "widgetlib never collects telemetry from your users.",
            "claim_type": "feature",
            "search_hints": ["telemetry", "emit_telemetry", "TELEMETRY_URL"],
        },
    ],
    "readme_path": str(FIXTURE_REPO / "README.md"),
}


_CHECKER_VERDICTS = {
    "verdicts": [
        # The checker would normally have to infer drift here; the stub
        # encodes the answer key. The trust-gate in
        # _enforce_trust_gate is what's actually under test for claim-03.
        {
            "claim_id": "claim-01",
            "claim_text": _PLANNER_CLAIMS["claims"][0]["claim_text"],
            "verdict": VERDICT_DRIFTED,
            "confidence": 0.85,
            "evidence_paths": ["pyproject.toml"],
            "notes": "README pins widgetlib==1.2.0; pyproject.toml ships version 0.9.0.",
        },
        {
            "claim_id": "claim-02",
            "claim_text": _PLANNER_CLAIMS["claims"][1]["claim_text"],
            "verdict": VERDICT_VERIFIED,
            "confidence": 0.95,
            "evidence_paths": ["widgetlib/widget.py", "widgetlib/__init__.py"],
            "notes": "Widget class with render() exists and is exported from package init.",
        },
        # claim-03: LLM hallucinates verified — trust gate must overwrite.
        {
            "claim_id": "claim-03",
            "claim_text": _PLANNER_CLAIMS["claims"][2]["claim_text"],
            "verdict": VERDICT_VERIFIED,
            "confidence": 0.9,
            "evidence_paths": ["widgetlib/__init__.py"],
            "notes": "Hallucinated by checker — the trust gate must overwrite this.",
        },
        {
            "claim_id": "claim-04",
            "claim_text": _PLANNER_CLAIMS["claims"][3]["claim_text"],
            "verdict": VERDICT_DRIFTED,
            "confidence": 0.8,
            "evidence_paths": ["widgetlib/cli.py"],
            "notes": "README documents --port; CLI implements --bind with default port 8000.",
        },
        {
            "claim_id": "claim-05",
            "claim_text": _PLANNER_CLAIMS["claims"][4]["claim_text"],
            "verdict": VERDICT_CONTRADICTED,
            "confidence": 0.9,
            "evidence_paths": ["pyproject.toml"],
            "notes": "pyproject.toml declares a jinja2 dependency.",
        },
        {
            "claim_id": "claim-06",
            "claim_text": _PLANNER_CLAIMS["claims"][5]["claim_text"],
            "verdict": VERDICT_CONTRADICTED,
            "confidence": 0.95,
            "evidence_paths": ["widgetlib/telemetry.py"],
            "notes": "widgetlib/telemetry.py emits events to a remote endpoint.",
        },
    ]
}


_CRITIC_RESPONSE = {
    "weak_verdicts": [],
    "missed_claims": [],
    "suggestions": ["Add CI step to run `openworkers audit readme` on every PR."],
    "overall_assessment": "Audit caught one verified, two drifted, two contradicted, one unsupported.",
}


def _make_stub_unified() -> UnifiedLLM:
    """Build a UnifiedLLM whose generate routes to a content-aware stub."""
    llm = UnifiedLLM()
    llm.dry_run = False  # bypass the placeholder path
    llm.set_available_providers(["anthropic"])

    async def fake_generate(
        provider: str,
        model: str,
        prompt: str,
        system_prompt: str,
        response_schema: Any,
    ) -> str:
        # Route by which agent's system prompt is in play.
        if "README PLANNER" in system_prompt:
            return json.dumps(_PLANNER_CLAIMS)
        if "README CHECKER" in system_prompt:
            return json.dumps(_CHECKER_VERDICTS)
        if "AUDIT CRITIC" in system_prompt:
            return json.dumps(_CRITIC_RESPONSE)
        return "{}"

    llm.set_generate_fn(fake_generate)
    return llm


@pytest.fixture
def stubbed_unified(monkeypatch) -> UnifiedLLM:
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("THESIS_QUALITY_PROVIDER", "anthropic")
    monkeypatch.setenv("THESIS_QUALITY_MODEL", "claude-sonnet-4-20250514")
    monkeypatch.setenv("THESIS_BALANCED_PROVIDER", "anthropic")
    monkeypatch.setenv("THESIS_BALANCED_MODEL", "claude-sonnet-4-20250514")
    monkeypatch.setenv("THESIS_CHEAP_PROVIDER", "anthropic")
    monkeypatch.setenv("THESIS_CHEAP_MODEL", "claude-sonnet-4-20250514")
    return _make_stub_unified()


@pytest.mark.asyncio
async def test_local_repo_adapter_finds_identifiers():
    adapter = LocalRepoAdapter(FIXTURE_REPO)
    # Real identifier present in source files. The adapter doesn't filter
    # the README — that's the orchestrator's job — so just check that
    # source-file hits exist in the result set.
    widget_hits = adapter.search_any(["Widget", "render"], limit=50)
    source_hits = [h for h in widget_hits if h.path.startswith("widgetlib/")]
    assert source_hits, "Widget/render must appear in source files, not only the README"
    # Fabricated env var: only the README mentions it; no source file does.
    debug_hits = adapter.search_any(["WIDGETLIB_DEBUG"], limit=50)
    assert all(h.path.endswith("README.md") for h in debug_hits), (
        "WIDGETLIB_DEBUG must not appear anywhere except the README — "
        "the orchestrator excludes the audited file so this becomes 'no evidence'."
    )


@pytest.mark.asyncio
async def test_readme_audit_end_to_end(stubbed_unified):
    orch = ReadmeAuditOrchestrator(unified=stubbed_unified)
    report, critique = await orch.audit(repo_path=FIXTURE_REPO)

    by_id: dict[str, Any] = {v.claim_id: v for v in report.verdicts}
    assert len(report.verdicts) == 6, "Planner stub seeded 6 claims"

    # Real claim with evidence → verified
    assert by_id["claim-02"].verdict == VERDICT_VERIFIED
    assert by_id["claim-02"].evidence_paths, "verified verdict must cite evidence"

    # Drifted: version pin and CLI flag
    assert by_id["claim-01"].verdict == VERDICT_DRIFTED
    assert by_id["claim-04"].verdict == VERDICT_DRIFTED

    # Trust gate: fabricated claim must be unsupported regardless of LLM output
    assert by_id["claim-03"].verdict == VERDICT_UNSUPPORTED, (
        "Trust gate failed: checker stub hallucinated 'verified' for a claim with "
        "no retrieved evidence, but _enforce_trust_gate should have overwritten it."
    )
    assert by_id["claim-03"].evidence_paths == []
    assert by_id["claim-03"].confidence == 0.0

    # Contradicted: zero-deps + no-telemetry
    assert by_id["claim-05"].verdict == VERDICT_CONTRADICTED
    assert by_id["claim-06"].verdict == VERDICT_CONTRADICTED

    # Summary tallies match
    assert report.summary[VERDICT_VERIFIED] == 1
    assert report.summary[VERDICT_DRIFTED] == 2
    assert report.summary[VERDICT_CONTRADICTED] == 2
    assert report.summary[VERDICT_UNSUPPORTED] == 1
    assert sum(report.summary.values()) == len(report.verdicts)

    # Critic ran
    assert critique.suggestions, "critic stub returned at least one suggestion"


@pytest.mark.asyncio
async def test_audit_handles_missing_readme(stubbed_unified, tmp_path):
    """A repo with no README must still produce a structured report, not crash."""
    (tmp_path / "src.py").write_text("print('hello')\n")
    orch = ReadmeAuditOrchestrator(unified=stubbed_unified)
    report, critique = await orch.audit(repo_path=tmp_path)
    assert report.verdicts == []
    assert report.errors == ["No README found in repo."]
    assert critique.weak_verdicts == []
