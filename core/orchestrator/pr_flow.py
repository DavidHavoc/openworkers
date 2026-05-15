"""PR audit orchestrator.

Verifies a pull request's description against its actual diff. Same
pipeline shape as ``ReadmeAuditOrchestrator``:

    planner (LLM)  →  researcher (deterministic grep over diff)
                   →  checker (LLM + trust gate)
                   →  critic (LLM adversarial pass)

The "researcher" is a content-aware grep on the unified diff via
``GitHubAdapter`` — no LLM call there, no network call once we have the
``PrSpec``. Token-free for tests.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from typing import Any, Callable

from core.orchestrator.audit_prompts import render_audit_prompt as _render_audit_prompt
from core.schemas_audit import (
    ALL_VERDICTS,
    VERDICT_UNSUPPORTED,
    AuditClaim,
    AuditCritique,
    AuditReport,
    ClaimEvidence,
    ClaimVerdict,
    EvidenceRef,
)
from core.sources.github import GitHubAdapter, PrSpec
from providers.code_audit_agents import (
    AuditCriticAgent,
    PrCheckerAgent,
    PrPlannerAgent,
)
from providers.unified import UnifiedLLM

logger = logging.getLogger(__name__)


class PrAuditOrchestrator:
    """Audit a PR description against the diff it carries."""

    def __init__(
        self,
        unified: UnifiedLLM,
        prompt_renderer: Callable[[str, dict[str, Any]], str] | None = None,
        max_evidence_per_claim: int = 5,
    ) -> None:
        self.unified = unified
        self.render = prompt_renderer or _render_audit_prompt
        self.max_evidence_per_claim = max_evidence_per_claim
        self.planner = PrPlannerAgent(unified=unified, prompt_renderer=self.render)
        self.checker = PrCheckerAgent(unified=unified, prompt_renderer=self.render)
        self.critic = AuditCriticAgent(unified=unified, prompt_renderer=self.render)

    async def audit(self, pr: PrSpec) -> tuple[AuditReport, AuditCritique]:
        start = time.time()
        adapter = GitHubAdapter(pr)
        errors: list[str] = []

        description = pr.description
        target = pr.url or f"{pr.owner}/{pr.repo}#{pr.number}"

        if not description.strip():
            empty = AuditReport(
                repo_path=f"{pr.owner}/{pr.repo}",
                target=target,
                verdicts=[],
                summary=dict.fromkeys(ALL_VERDICTS, 0),
                errors=["PR description is empty — nothing to audit."],
            )
            return empty, AuditCritique()

        # ── Stage 1: Planner extracts claims from PR title + body ──
        try:
            planner_result = await self.planner.execute(
                pr_description=description,
                pr_url=pr.url,
                changed_files=pr.changed_files,
            )
            claim_list = planner_result["output"]
        except Exception as e:
            errors.append(f"planner: {e}")
            from core.schemas_audit import AuditClaimList

            claim_list = AuditClaimList(claims=[], readme_path=target)

        # ── Stage 2: Researcher retrieves diff evidence (deterministic) ──
        evidence = await asyncio.to_thread(self._retrieve_all_evidence, claim_list.claims, adapter)

        # ── Stage 3: Checker renders verdicts (with trust gate) ──
        if claim_list.claims:
            try:
                checker_result = await self.checker.execute(claim_list.claims, evidence)
                verdicts: list[ClaimVerdict] = list(checker_result["output"].verdicts)
            except Exception as e:
                errors.append(f"checker: {e}")
                verdicts = [
                    ClaimVerdict(
                        claim_id=c.claim_id,
                        claim_text=c.claim_text,
                        verdict=VERDICT_UNSUPPORTED,
                        confidence=0.0,
                        evidence_paths=[],
                        notes=f"Checker failed: {e}",
                    )
                    for c in claim_list.claims
                ]
        else:
            verdicts = []

        # ── Stage 4: Critic adversarial pass ──
        try:
            critic_result = await self.critic.execute(verdicts, description)
            critique: AuditCritique = critic_result["output"]
        except Exception as e:
            errors.append(f"critic: {e}")
            critique = AuditCritique()

        summary = Counter(v.verdict for v in verdicts)
        report = AuditReport(
            repo_path=f"{pr.owner}/{pr.repo}",
            target=target,
            verdicts=verdicts,
            summary={v: int(summary.get(v, 0)) for v in ALL_VERDICTS},
            errors=errors,
        )

        elapsed_ms = int((time.time() - start) * 1000)
        logger.info(
            "pr_audit completed pr=%s claims=%d elapsed_ms=%d errors=%d",
            target,
            len(verdicts),
            elapsed_ms,
            len(errors),
        )
        return report, critique

    def _retrieve_all_evidence(
        self,
        claims: list[AuditClaim],
        adapter: GitHubAdapter,
    ) -> list[ClaimEvidence]:
        out: list[ClaimEvidence] = []
        for claim in claims:
            snippets = adapter.search_any(claim.search_hints, limit=self.max_evidence_per_claim)
            refs = [
                EvidenceRef(
                    path=s.path,
                    line_start=s.line_start,
                    line_end=s.line_end,
                    text=s.text,
                    source=s.source,
                )
                for s in snippets
            ]
            out.append(ClaimEvidence(claim_id=claim.claim_id, snippets=refs))
        return out


def format_pr_report_text(
    report: AuditReport,
    critique: AuditCritique | None = None,
) -> str:
    """Pretty-print a PR audit report for terminal output."""
    lines: list[str] = []
    lines.append(f"PR audit — {report.target or report.repo_path}")
    lines.append("")
    if report.summary:
        summary_line = "  ".join(f"{k}={v}" for k, v in report.summary.items())
        lines.append(f"Summary: {summary_line}")
        lines.append("")
    for v in report.verdicts:
        marker = {
            "verified": "✓",
            "drifted": "≠",
            "contradicted": "✗",
            "unsupported": "?",
        }.get(v.verdict, "·")
        lines.append(f"{marker} [{v.verdict.upper():12s}] {v.claim_id}: {v.claim_text}")
        for p in v.evidence_paths:
            lines.append(f"      ↳ {p}")
        if v.notes:
            lines.append(f"      note: {v.notes}")
    if report.errors:
        lines.append("")
        lines.append("Errors:")
        for e in report.errors:
            lines.append(f"  - {e}")
    if critique is not None:
        lines.append("")
        lines.append("Critic pass:")
        for wv in critique.weak_verdicts:
            lines.append(f"  weak:    {wv}")
        for mc in critique.missed_claims:
            lines.append(f"  missed:  {mc}")
        for sg in critique.suggestions:
            lines.append(f"  suggest: {sg}")
        if critique.overall_assessment:
            lines.append(f"  → {critique.overall_assessment}")
    return "\n".join(lines)
