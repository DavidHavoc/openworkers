"""Pydantic schemas for the code-audit domain.

Kept separate from ``core/schemas.py`` so the legacy thesis types and the
new audit types evolve independently while the two domains coexist.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

VERDICT_VERIFIED = "verified"
VERDICT_DRIFTED = "drifted"
VERDICT_UNSUPPORTED = "unsupported"
VERDICT_CONTRADICTED = "contradicted"

ALL_VERDICTS = (
    VERDICT_VERIFIED,
    VERDICT_DRIFTED,
    VERDICT_UNSUPPORTED,
    VERDICT_CONTRADICTED,
)


class ReadmeClaim(BaseModel):
    """A single atomic factual claim extracted from a README."""

    claim_id: str
    claim_text: str = Field(description="Verbatim quote from the README")
    claim_type: str = Field(
        default="other",
        description="feature | install | usage | requirement | metric | api | other",
    )
    search_hints: list[str] = Field(
        default_factory=list,
        description="Tokens/identifiers the researcher should grep for",
    )


class ReadmeClaimList(BaseModel):
    claims: list[ReadmeClaim] = Field(default_factory=list)
    readme_path: str = ""


# Cross-domain aliases. README and PR claims share the same shape; the
# differing-in-name dance is purely for call-site readability. When a
# third auditor lands we'll rename ReadmeClaim → AuditClaim cleanly;
# until then the alias keeps PR code self-explanatory without churning
# the README slice's imports.
AuditClaim = ReadmeClaim
AuditClaimList = ReadmeClaimList


class EvidenceRef(BaseModel):
    """Adapter-agnostic citation handle. Mirrors ``EvidenceSnippet``
    in shape but is the JSON-serialisable form passed across the
    LLM boundary.
    """

    path: str
    line_start: int = 0
    line_end: int = 0
    text: str = ""
    source: str = ""


class ClaimEvidence(BaseModel):
    claim_id: str
    snippets: list[EvidenceRef] = Field(default_factory=list)


class ClaimVerdict(BaseModel):
    claim_id: str
    claim_text: str = ""
    verdict: str = Field(description="verified | drifted | unsupported | contradicted")
    confidence: float = 0.0
    evidence_paths: list[str] = Field(default_factory=list)
    notes: str = ""


class ClaimVerdictList(BaseModel):
    """LLM-side wrapper so the checker emits a single JSON object."""

    verdicts: list[ClaimVerdict] = Field(default_factory=list)


class AuditReport(BaseModel):
    repo_path: str
    readme_path: str = ""
    # Free-form identifier of the audited artefact (PR URL, doc path, …).
    # Kept optional so the README slice can leave it blank and stay
    # backwards-compatible with the prior shape.
    target: str = ""
    verdicts: list[ClaimVerdict] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class AuditCritique(BaseModel):
    weak_verdicts: list[str] = Field(default_factory=list)
    missed_claims: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    overall_assessment: str = ""
