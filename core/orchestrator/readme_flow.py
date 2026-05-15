"""README audit orchestrator.

Parallels ``ThesisOrchestrator`` in spirit (planner → researcher →
checker → critic) but with three deliberate differences:

1. The researcher is **deterministic Python**, not an LLM. Evidence
   retrieval is a grep over the local repo via ``LocalRepoAdapter`` —
   no fabrication risk, no per-claim API cost.

2. The trustworthiness gate is enforced in code (``_enforce_trust_gate``
   in ``providers/code_audit_agents.py``), not entrusted to a prompt.

3. There is no shared blackboard yet — claim/evidence state flows as
   plain Python between agents. The blackboard layer will fold in
   when a second audit type (PR / compliance) is added and the
   shared state actually buys something.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from core.schemas_audit import (
    ALL_VERDICTS,
    VERDICT_UNSUPPORTED,
    AuditCritique,
    AuditReport,
    ClaimEvidence,
    ClaimVerdict,
    EvidenceRef,
    ReadmeClaim,
    ReadmeClaimList,
)
from core.sources.local_repo import LocalRepoAdapter
from providers.code_audit_agents import (
    AuditCriticAgent,
    ReadmeCheckerAgent,
    ReadmePlannerAgent,
)
from providers.unified import UnifiedLLM

logger = logging.getLogger(__name__)


_AUDIT_PROMPT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "prompts",
    "code_audit",
)

_TEMPLATE_FILES = {
    "readme_planner": "readme_planner.md",
    "readme_checker": "readme_checker.md",
    "audit_critic": "audit_critic.md",
}


def _render_audit_prompt(name: str, variables: dict[str, Any]) -> str:
    """Tiny placeholder-substitution renderer for audit prompts.

    Deliberately not reusing PromptCompiler: that compiler is wired to
    extract blackboard state, which this slice doesn't use. Audit
    templates only need ``{{ var }}`` substitution.
    """
    filename = _TEMPLATE_FILES.get(name)
    if not filename:
        raise ValueError(f"Unknown audit template: {name}")
    path = os.path.join(_AUDIT_PROMPT_DIR, filename)
    try:
        with open(path, encoding="utf-8") as f:
            template = f.read()
    except OSError:
        return f"[Template {name} not found at {path}]"
    for key, value in variables.items():
        template = template.replace("{{ " + key + " }}", str(value))
    return template


class ReadmeAuditOrchestrator:
    """Run a README audit end-to-end against a local repo."""

    def __init__(
        self,
        unified: UnifiedLLM,
        adapter: LocalRepoAdapter | None = None,
        prompt_renderer: Callable[[str, dict[str, Any]], str] | None = None,
        max_evidence_per_claim: int = 5,
    ) -> None:
        self.unified = unified
        self.adapter = adapter
        self.render = prompt_renderer or _render_audit_prompt
        self.max_evidence_per_claim = max_evidence_per_claim
        self.planner = ReadmePlannerAgent(unified=unified, prompt_renderer=self.render)
        self.checker = ReadmeCheckerAgent(unified=unified, prompt_renderer=self.render)
        self.critic = AuditCriticAgent(unified=unified, prompt_renderer=self.render)

    async def audit(
        self,
        repo_path: Path | str,
        readme_path: Path | str | None = None,
    ) -> tuple[AuditReport, AuditCritique]:
        start = time.time()
        adapter = self.adapter or LocalRepoAdapter(repo_path)
        # Re-bind adapter when caller passed an explicit repo_path: the
        # default-construction branch above already pinned it; this
        # branch handles the case where the caller reused an existing
        # orchestrator across multiple repos.
        if self.adapter is None:
            self.adapter = adapter

        readme_file = Path(readme_path) if readme_path else adapter.find_readme()
        errors: list[str] = []
        if readme_file is None or not Path(readme_file).is_file():
            empty = AuditReport(
                repo_path=str(adapter.root),
                readme_path="",
                verdicts=[],
                summary=dict.fromkeys(ALL_VERDICTS, 0),
                errors=["No README found in repo."],
            )
            return empty, AuditCritique()

        readme_path_str = str(Path(readme_file).resolve())
        readme_text = Path(readme_file).read_text(encoding="utf-8", errors="replace")
        # The README under audit must not count as evidence for its own
        # claims — otherwise every fabricated claim 'verifies' itself.
        try:
            readme_rel = str(Path(readme_path_str).resolve().relative_to(adapter.root))
        except ValueError:
            readme_rel = ""

        # ── Stage 1: Planner extracts claims ──
        try:
            planner_result = await self.planner.execute(readme_text, readme_path_str)
            claim_list: ReadmeClaimList = planner_result["output"]
        except Exception as e:
            errors.append(f"planner: {e}")
            claim_list = ReadmeClaimList(claims=[], readme_path=readme_path_str)

        # ── Stage 2: Researcher retrieves evidence (deterministic) ──
        evidence = await asyncio.to_thread(
            self._retrieve_all_evidence, claim_list.claims, adapter, readme_rel
        )

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
            critic_result = await self.critic.execute(verdicts, readme_text)
            critique: AuditCritique = critic_result["output"]
        except Exception as e:
            errors.append(f"critic: {e}")
            critique = AuditCritique()

        summary = Counter(v.verdict for v in verdicts)
        report = AuditReport(
            repo_path=str(adapter.root),
            readme_path=readme_path_str,
            verdicts=verdicts,
            summary={v: int(summary.get(v, 0)) for v in ALL_VERDICTS},
            errors=errors,
        )
        elapsed_ms = int((time.time() - start) * 1000)
        logger.info(
            "readme_audit completed repo=%s claims=%d elapsed_ms=%d errors=%d",
            adapter.root,
            len(verdicts),
            elapsed_ms,
            len(errors),
        )
        return report, critique

    def _retrieve_all_evidence(
        self,
        claims: list[ReadmeClaim],
        adapter: LocalRepoAdapter,
        exclude_path: str = "",
    ) -> list[ClaimEvidence]:
        out: list[ClaimEvidence] = []
        for claim in claims:
            # Over-fetch then post-filter so excluding the README doesn't
            # silently shrink the result set below ``max_evidence_per_claim``.
            raw = adapter.search_any(
                claim.search_hints,
                limit=(
                    self.max_evidence_per_claim * 2 if exclude_path else self.max_evidence_per_claim
                ),
            )
            filtered = [s for s in raw if s.path != exclude_path][: self.max_evidence_per_claim]
            refs = [
                EvidenceRef(
                    path=s.path,
                    line_start=s.line_start,
                    line_end=s.line_end,
                    text=s.text,
                    source=s.source,
                )
                for s in filtered
            ]
            out.append(ClaimEvidence(claim_id=claim.claim_id, snippets=refs))
        return out


def format_report_text(report: AuditReport, critique: AuditCritique | None = None) -> str:
    """Pretty-print an audit report for terminal output."""
    lines: list[str] = []
    lines.append(f"README audit — {report.repo_path}")
    lines.append(f"README: {report.readme_path or '(not found)'}")
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
        if v.evidence_paths:
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
