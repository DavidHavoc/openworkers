"""Code-audit agent suite.

Mirrors the structure of ``providers/thesis_agents.py`` so the
orchestrator-level contract (each agent has ``execute(task, context) ->
dict``) stays uniform across domains. The thesis path remains untouched
while this slice lands.

Trustworthiness gates live here, not in prompts: any claim with no
retrieved evidence is forced to ``unsupported`` *after* the LLM
responds. The LLM never gets to invent a ``verified`` verdict for a
claim with zero supporting snippets.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Callable

from pydantic import BaseModel

from core.schemas_audit import (
    ALL_VERDICTS,
    VERDICT_UNSUPPORTED,
    AuditCritique,
    ClaimEvidence,
    ClaimVerdict,
    ClaimVerdictList,
    ReadmeClaim,
    ReadmeClaimList,
)
from providers.unified import UnifiedLLM

logger = logging.getLogger(__name__)


__all__ = [
    "AuditCriticAgent",
    "PrCheckerAgent",
    "PrPlannerAgent",
    "ReadmeCheckerAgent",
    "ReadmePlannerAgent",
    "_schema_for",
]


_MODEL_SCHEMAS: dict[type[BaseModel], dict[str, Any]] = {}


def _schema_for(model_cls: type[BaseModel]) -> dict[str, Any]:
    if model_cls not in _MODEL_SCHEMAS:
        raw = model_cls.model_json_schema()
        raw.pop("title", None)
        _MODEL_SCHEMAS[model_cls] = raw
    return _MODEL_SCHEMAS[model_cls]


def _parse_json_lenient(text: str) -> Any:
    if not text or not text.strip():
        return {}
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            return json.loads(cleaned.replace("'", '"'))
        except json.JSONDecodeError:
            logger.warning("Could not parse audit JSON: %s", text[:200])
            return {}


def _parse_structured(text: str, model_cls: type[BaseModel]) -> Any:
    if not text or not text.strip():
        return model_cls()
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    for attempt in (cleaned, cleaned.replace("'", '"')):
        try:
            return model_cls.model_validate_json(attempt)
        except Exception:
            continue
    parsed = _parse_json_lenient(cleaned)
    try:
        return model_cls.model_validate(parsed)
    except Exception:
        return model_cls()


class ReadmePlannerAgent:
    """Extracts atomic factual claims from a README.

    Stateless: the orchestrator hands it the README text and a system
    prompt; it returns ``ReadmeClaimList``. No blackboard reads here —
    the README is the entire input and we want the prompt to be
    deterministic and cacheable on its content.
    """

    def __init__(self, unified: UnifiedLLM, prompt_renderer: Callable[[str, dict[str, Any]], str]):
        self.unified = unified
        self.render = prompt_renderer

    async def execute(
        self,
        readme_text: str,
        readme_path: str,
    ) -> dict[str, Any]:
        system_prompt = self.render("readme_planner", {"readme_path": readme_path})
        user_prompt = (
            "Extract every atomic factual claim from the README below. "
            "Return a JSON object matching the ReadmeClaimList schema.\n\n"
            f"README path: {readme_path}\n\n"
            "---BEGIN README---\n"
            f"{readme_text}\n"
            "---END README---"
        )
        response = await self.unified.generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            mode="quality",
            response_schema=_schema_for(ReadmeClaimList),
        )
        parsed = _parse_structured(response.content, ReadmeClaimList)
        parsed = _normalise_claim_list(parsed, readme_path)
        return {
            "agent": "readme_planner",
            "tier": "head",
            "status": "success",
            "output": parsed,
            "provider": response.provider_used,
            "model": response.model,
            "latency_ms": response.latency_ms,
            "cost_estimate_usd": response.cost_estimate_usd,
            "dry_run": response.dry_run,
            "fallback_used": response.fallback_used,
        }


def _normalise_claim_list(claim_list: ReadmeClaimList, readme_path: str) -> ReadmeClaimList:
    """Fill in claim_ids and search_hints when the planner omits them.

    The schema permits empty strings, but downstream agents key off
    ``claim_id``. Generating ids here keeps the LLM template lenient
    without losing the invariant that every claim is addressable.
    """
    if not claim_list.readme_path:
        claim_list.readme_path = readme_path
    fixed: list[ReadmeClaim] = []
    for idx, claim in enumerate(claim_list.claims):
        cid = claim.claim_id or f"claim-{idx + 1:02d}"
        hints = list(claim.search_hints) if claim.search_hints else _derive_hints(claim.claim_text)
        fixed.append(
            ReadmeClaim(
                claim_id=cid,
                claim_text=claim.claim_text,
                claim_type=claim.claim_type or "other",
                search_hints=hints,
            )
        )
    claim_list.claims = fixed
    return claim_list


_HINT_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_./-]{2,}")
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "from",
    "into",
    "you",
    "your",
    "via",
    "use",
    "uses",
    "using",
    "can",
    "will",
    "are",
    "was",
    "were",
    "all",
    "any",
    "one",
    "two",
    "three",
    "have",
    "has",
    "had",
}


def _derive_hints(text: str) -> list[str]:
    """Fallback hint extraction when the planner skips ``search_hints``."""
    hints: list[str] = []
    seen: set[str] = set()
    for tok in _HINT_TOKEN.findall(text):
        if tok.lower() in _STOPWORDS:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        hints.append(tok)
        if len(hints) >= 6:
            break
    return hints


class ReadmeCheckerAgent:
    """Renders verdicts for each (claim, evidence) pair.

    Critically, after the LLM returns, we *enforce* the trustworthiness
    gate: any claim whose evidence list is empty is forced to
    ``unsupported`` regardless of what the LLM said. This is the
    invariant the project is built around: no verdict without
    evidence.
    """

    def __init__(self, unified: UnifiedLLM, prompt_renderer: Callable[[str, dict[str, Any]], str]):
        self.unified = unified
        self.render = prompt_renderer

    async def execute(
        self,
        claims: list[ReadmeClaim],
        evidence: list[ClaimEvidence],
    ) -> dict[str, Any]:
        evidence_by_claim = {e.claim_id: e for e in evidence}
        evidence_payload = [e.model_dump() for e in evidence]
        claims_payload = [c.model_dump() for c in claims]

        system_prompt = self.render(
            "readme_checker",
            {"verdict_values": " | ".join(ALL_VERDICTS)},
        )
        user_prompt = (
            "Judge each claim against its retrieved evidence and return a "
            "ClaimVerdictList JSON object.\n\n"
            f"CLAIMS:\n{json.dumps(claims_payload, indent=2)}\n\n"
            f"EVIDENCE:\n{json.dumps(evidence_payload, indent=2)}"
        )
        response = await self.unified.generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            mode="balanced",
            response_schema=_schema_for(ClaimVerdictList),
        )
        parsed = _parse_structured(response.content, ClaimVerdictList)

        verdicts = _enforce_trust_gate(parsed.verdicts, claims, evidence_by_claim)
        parsed.verdicts = verdicts

        return {
            "agent": "readme_checker",
            "tier": "middle",
            "status": "success",
            "output": parsed,
            "provider": response.provider_used,
            "model": response.model,
            "latency_ms": response.latency_ms,
            "cost_estimate_usd": response.cost_estimate_usd,
            "dry_run": response.dry_run,
            "fallback_used": response.fallback_used,
        }


def _enforce_trust_gate(
    raw_verdicts: list[ClaimVerdict],
    claims: list[ReadmeClaim],
    evidence_by_claim: dict[str, ClaimEvidence],
) -> list[ClaimVerdict]:
    """For each claim, ensure exactly one verdict exists and that a
    no-evidence verdict is *always* ``unsupported``.

    Even if the LLM hallucinates a confident ``verified`` for a claim
    with zero retrieved snippets, this function overwrites it. This is
    the project's core invariant — refusing to verdict without
    evidence — encoded as code, not a prompt instruction.
    """
    by_id: dict[str, ClaimVerdict] = {v.claim_id: v for v in raw_verdicts}
    out: list[ClaimVerdict] = []
    for claim in claims:
        ev = evidence_by_claim.get(claim.claim_id)
        snippets = ev.snippets if ev else []
        verdict = by_id.get(claim.claim_id)
        if not verdict:
            verdict = ClaimVerdict(
                claim_id=claim.claim_id,
                claim_text=claim.claim_text,
                verdict=VERDICT_UNSUPPORTED,
                confidence=0.0,
                evidence_paths=[],
                notes="No verdict returned by checker; defaulted to unsupported.",
            )
        if not snippets:
            # Hard reset: an unsupported verdict must carry no positive
            # signal forward — zero confidence, no cited paths. If the LLM
            # left a note suggesting verification, replace it with the
            # honest one.
            verdict.verdict = VERDICT_UNSUPPORTED
            verdict.evidence_paths = []
            verdict.confidence = 0.0
            verdict.notes = "No supporting evidence found in the repository."
        else:
            existing_paths = [
                p for p in (verdict.evidence_paths or []) if any(s.path == p for s in snippets)
            ]
            if not existing_paths:
                existing_paths = [s.path for s in snippets]
            verdict.evidence_paths = existing_paths
        if verdict.verdict not in ALL_VERDICTS:
            verdict.verdict = VERDICT_UNSUPPORTED
        if not verdict.claim_text:
            verdict.claim_text = claim.claim_text
        out.append(verdict)
    return out


class AuditCriticAgent:
    """Adversarial pass over the verdict list.

    Looks for over-confident verdicts, weak evidence chains, and
    surfaces likely missed claims. Stateless wrt the blackboard; takes
    the verdict list and original README as input.
    """

    def __init__(self, unified: UnifiedLLM, prompt_renderer: Callable[[str, dict[str, Any]], str]):
        self.unified = unified
        self.render = prompt_renderer

    async def execute(
        self,
        verdicts: list[ClaimVerdict],
        readme_text: str,
    ) -> dict[str, Any]:
        system_prompt = self.render("audit_critic", {})
        verdicts_payload = [v.model_dump() for v in verdicts]
        user_prompt = (
            "Critique the verdict list. Identify weak verdicts (low confidence "
            "or shaky evidence), missed claims (factual statements in the README "
            "the planner did not capture), and concrete suggestions. Return an "
            "AuditCritique JSON object.\n\n"
            f"VERDICTS:\n{json.dumps(verdicts_payload, indent=2)}\n\n"
            "---BEGIN README---\n"
            f"{readme_text}\n"
            "---END README---"
        )
        response = await self.unified.generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            mode="quality",
            response_schema=_schema_for(AuditCritique),
        )
        parsed = _parse_structured(response.content, AuditCritique)
        return {
            "agent": "audit_critic",
            "tier": "head",
            "status": "success",
            "output": parsed,
            "provider": response.provider_used,
            "model": response.model,
            "latency_ms": response.latency_ms,
            "cost_estimate_usd": response.cost_estimate_usd,
            "dry_run": response.dry_run,
            "fallback_used": response.fallback_used,
        }


def new_claim_id() -> str:
    return f"claim-{uuid.uuid4().hex[:8]}"


# ──────────────────────────────────────────────────────────────────────
# PR-audit agents
#
# Parallel to the README ones rather than a shared base class: per
# AGENTS.md, two examples is not yet a pattern. When the third auditor
# (compliance) lands, ``ClaimPlannerAgent`` / ``ClaimCheckerAgent`` can
# absorb both — but premature extraction here would commit us to an
# interface we'd inevitably regret.
# ──────────────────────────────────────────────────────────────────────


class PrPlannerAgent:
    """Extracts atomic factual claims from a PR title + body."""

    def __init__(self, unified: UnifiedLLM, prompt_renderer: Callable[[str, dict[str, Any]], str]):
        self.unified = unified
        self.render = prompt_renderer

    async def execute(
        self,
        pr_description: str,
        pr_url: str = "",
        changed_files: list[str] | None = None,
    ) -> dict[str, Any]:
        changed_files = changed_files or []
        system_prompt = self.render(
            "pr_planner",
            {"pr_url": pr_url, "files_changed": ", ".join(changed_files[:25])},
        )
        user_prompt = (
            "Extract every atomic factual claim from the PR description below. "
            "Return a JSON object matching the ReadmeClaimList schema (same shape, "
            "claim_type drawn from add | remove | fix | refactor | test | behavior | "
            "doc | other).\n\n"
            f"PR URL: {pr_url}\n"
            f"Changed files ({len(changed_files)}): "
            f"{', '.join(changed_files[:25]) if changed_files else '(none)'}\n\n"
            "---BEGIN PR DESCRIPTION---\n"
            f"{pr_description}\n"
            "---END PR DESCRIPTION---"
        )
        response = await self.unified.generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            mode="quality",
            response_schema=_schema_for(ReadmeClaimList),
        )
        parsed = _parse_structured(response.content, ReadmeClaimList)
        parsed = _normalise_claim_list(parsed, pr_url)
        return {
            "agent": "pr_planner",
            "tier": "head",
            "status": "success",
            "output": parsed,
            "provider": response.provider_used,
            "model": response.model,
            "latency_ms": response.latency_ms,
            "cost_estimate_usd": response.cost_estimate_usd,
            "dry_run": response.dry_run,
            "fallback_used": response.fallback_used,
        }


class PrCheckerAgent:
    """Renders verdicts for PR claims against diff evidence.

    Identical trust-gate semantics as ``ReadmeCheckerAgent``: any claim
    with no retrieved hunk evidence is forced to ``unsupported`` after
    the LLM responds.
    """

    def __init__(self, unified: UnifiedLLM, prompt_renderer: Callable[[str, dict[str, Any]], str]):
        self.unified = unified
        self.render = prompt_renderer

    async def execute(
        self,
        claims: list[ReadmeClaim],
        evidence: list[ClaimEvidence],
    ) -> dict[str, Any]:
        evidence_by_claim = {e.claim_id: e for e in evidence}
        evidence_payload = [e.model_dump() for e in evidence]
        claims_payload = [c.model_dump() for c in claims]

        system_prompt = self.render(
            "pr_checker",
            {"verdict_values": " | ".join(ALL_VERDICTS)},
        )
        user_prompt = (
            "Judge each PR claim against its retrieved diff evidence and return "
            "a ClaimVerdictList JSON object.\n\n"
            f"CLAIMS:\n{json.dumps(claims_payload, indent=2)}\n\n"
            f"DIFF EVIDENCE:\n{json.dumps(evidence_payload, indent=2)}"
        )
        response = await self.unified.generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            mode="balanced",
            response_schema=_schema_for(ClaimVerdictList),
        )
        parsed = _parse_structured(response.content, ClaimVerdictList)
        verdicts = _enforce_trust_gate(parsed.verdicts, claims, evidence_by_claim)
        parsed.verdicts = verdicts
        return {
            "agent": "pr_checker",
            "tier": "middle",
            "status": "success",
            "output": parsed,
            "provider": response.provider_used,
            "model": response.model,
            "latency_ms": response.latency_ms,
            "cost_estimate_usd": response.cost_estimate_usd,
            "dry_run": response.dry_run,
            "fallback_used": response.fallback_used,
        }
