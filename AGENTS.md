# Agents Guide

> Onboarding for future agents (and humans new to the repo). Read this before touching code so you don't accidentally re-litigate decisions that already shipped.

## Project north star

openworkers is **the multi-agent system that refuses to make things up**. Every claim the system emits is either tied to a verifiable primary source or marked as unsupported. Two domains live in the codebase:

1. **Thesis assistant** (the legacy flagship). Audits literature claims against arXiv / Semantic Scholar / CrossRef. Producing prose is explicitly out of scope — every output is structured JSON.
2. **Code audit** (the new flagship, in progress). Audits factual claims in technical artefacts (READMEs first, then PRs, compliance docs, architecture docs) against the actual codebase, language specs, and dependencies.

The two domains share the same DNA: planner → researcher → checker → critic pipelines, structured output everywhere, a hard trust gate that *refuses* to verdict without evidence.

## Where things are

```
core/
  blackboard/        # Redis-backed shared state (thesis-only for now)
  orchestrator/
    thesis_flow.py   # ThesisOrchestrator — legacy, do not break
    readme_flow.py   # ReadmeAuditOrchestrator — new code-audit slice
    compiler.py      # PromptCompiler for thesis (blackboard → prompt vars)
  router/            # Provider tier routing (quality/balanced/cheap)
  memory/episodic.py # Qdrant episodic memory (thesis)
  schemas.py         # Thesis Pydantic models
  schemas_audit.py   # Code-audit Pydantic models (kept separate on purpose)
  sources/
    base.py          # SourceAdapter ABC — the new evidence-backend contract
    local_repo.py    # LocalRepoAdapter — grep over a local repo
providers/
  unified.py         # UnifiedLLM: provider fallback, breakers, DRY_RUN path
  thesis_agents.py   # Thesis agent suite — untouched, keep passing
  code_audit_agents.py # README planner, checker, critic + trust gate
  budget.py          # BudgetGuard (contextvars-scoped session ceiling)
  resilience.py      # Tenacity + pybreaker glue
prompts/
  *.md               # Thesis templates (head_planner, specialist_*, ...)
  code_audit/*.md    # Audit templates (readme_planner, readme_checker, ...)
tools/mcp/           # Literature MCP tools; will migrate behind SourceAdapter
apps/
  cli/main.py        # Single argparse CLI for both `thesis ...` and `audit ...`
  api/               # FastAPI surface
  mcp_server/        # MCP stdio server
  worker/            # Async worker stub
tests/
  fixtures/sample_repo/   # Synthetic widgetlib repo for audit tests
  code_audit/             # New audit tests
  test_*.py               # Thesis tests — DO NOT regress
```

## The trust gate (read this twice)

For code audit, the invariant **"no verdict without evidence"** is enforced in code, not in prompts. In `providers/code_audit_agents.py::_enforce_trust_gate`:

```
for each claim:
    if retrieved evidence is empty:
        verdict = "unsupported"
        confidence = 0.0
        evidence_paths = []
        notes = "No supporting evidence found in the repository."
```

This overwrites whatever the LLM said. A confidently hallucinating checker that returns `verified` for a claim with zero evidence gets corrected before the user ever sees the report. Do **not** move this logic into a prompt. The test `test_readme_audit_end_to_end` in `tests/code_audit/test_readme_flow.py` explicitly seeds a hallucinating checker stub and asserts the override fires.

Mirror this pattern when you add new auditors (PR auditor, compliance auditor, etc.): keep the LLM creative, but enforce the trust invariant in Python.

## The README-audit flow (current slice)

1. **Planner (LLM)** — reads the README, extracts atomic factual claims with verbatim quotes + grep-friendly search hints. Schema: `ReadmeClaimList`.
2. **Researcher (deterministic Python)** — uses `LocalRepoAdapter.search_any(hints)` to retrieve evidence snippets from the repo. **No LLM call here** — it's just a filesystem grep with safety rails (path traversal guard, file-size cap, dir excludes).
3. **Checker (LLM + trust gate)** — judges each `(claim, evidence)` pair into `verified | drifted | unsupported | contradicted`. Trust gate runs after.
4. **Critic (LLM)** — adversarial pass: weak verdicts, missed claims, suggestions.

The audited README is **excluded** from its own evidence pool — otherwise every fabricated claim could "verify itself" against the README quote. See `ReadmeAuditOrchestrator.audit` for the exclusion logic.

## Coexistence rules

- **Do not break the thesis path.** The full thesis test suite (`tests/test_*.py` minus `tests/code_audit/`) must stay green. Thesis is being deprecated *gradually*, not yanked.
- **Do not modify `core/schemas.py` to add audit fields.** `core/schemas_audit.py` is the audit-domain home. The two domains evolve independently until a real reason to merge appears.
- **Blackboard is thesis-only for now.** README audit deliberately skips it — claim/evidence flow is plain Python. When a second audit type ships and shared state actually buys something, fold the blackboard in.

## LLM routing & DRY_RUN

- `UnifiedLLM` (in `providers/unified.py`) routes to Anthropic / OpenAI / DeepSeek by tier (quality / balanced / cheap), with per-provider circuit breakers and a fallback chain.
- Tests run with `DRY_RUN=true` by default (from `Settings`). Under DRY_RUN, `generate()` returns a placeholder JSON shaped from the `response_schema`. **Caveat:** array fields come back empty — useful for "did the wiring work?" smoke checks, useless for end-to-end behaviour.
- For end-to-end tests of new agent flows, do **not** rely on DRY_RUN. Set `DRY_RUN=false`, set the `THESIS_<TIER>_PROVIDER` / `_MODEL` env vars, and stub LLM responses via `UnifiedLLM.set_generate_fn(...)` — content-aware (route by which agent's system prompt is in play). See `tests/code_audit/test_readme_flow.py::_make_stub_unified` for the pattern.

## Conventions

- **No prose generation.** Every agent emits structured JSON validated against a Pydantic model. If you find yourself prompting for "a paragraph that summarises…", stop and write a schema instead.
- **Structured output schemas** are derived from Pydantic models via `_schema_for()` (one per file: see `providers/thesis_agents.py` and `providers/code_audit_agents.py`).
- **`from __future__ import annotations`** at the top of every new file. The project targets Python 3.9 but uses py3.10+ syntax via deferred evaluation.
- **Lint stack:** `ruff` + `black --line-length=100`, both run in CI. New files must pass both. `mypy` strict is enforced on `core/` and `providers/`.
- **Comments:** non-obvious *why* only. No "this function reads a file"–style narration. Comments explaining hidden invariants, past incidents, or workarounds are welcome.
- **Commit hygiene:** no `--no-verify`, no skipping hooks. Pre-commit hook failures are diagnostic signals, not nuisances to bypass.

## Where the project is going (1.0 trajectory)

See `ROADMAP.md` for the full picture. Short version:

- ✅ Slice 1 (shipped): README auditor.
- 🚧 Next slices: PR auditor (`audit pr <url>`), compliance auditor, architecture auditor. All slot in behind the same `SourceAdapter` + agent-suite + trust-gate pattern.
- 🚧 Layered source adapters: repo (highest trust) → language specs / RFCs → dependency source. The literature MCP tools will migrate behind the same contract.
- 🚧 Cherry-picked from the v1.0 plan: tool/source registry, light provider-registry abstraction (Ollama later for local inference on private repos), structlog audit trail.
- ⏸️ Deferred: PyPI packaging, Typer CLI rewrite, OTel, Smart truncation, Ollama. Not blocking the audit-track expansion.

The thesis pipeline stays first-class through the transition, then is gradually deprecated as code-audit reaches feature parity.

## How to add a new auditor (recipe)

1. **Schema** — add audit-specific Pydantic models to `core/schemas_audit.py` (claim shape, verdict shape, report shape).
2. **Source adapter** — if a new evidence backend is needed (e.g., GitHub PR adapter), add a class to `core/sources/` implementing `SourceAdapter`. Keep the path-traversal / scope guard at the adapter boundary.
3. **Agents** — add `<Domain>PlannerAgent`, `<Domain>CheckerAgent`, `AuditCriticAgent` (or reuse) to `providers/code_audit_agents.py` or a sibling module. The checker's post-LLM step **must** call a trust gate equivalent to `_enforce_trust_gate`.
4. **Orchestrator** — add `core/orchestrator/<domain>_flow.py` following `readme_flow.py`. Stage order is planner → deterministic researcher → checker (+ gate) → critic. Exclude the audited artefact from its own evidence pool if applicable.
5. **Prompts** — add `prompts/code_audit/<domain>_*.md` templates with explicit JSON schemas in the body and "no prose, no markdown fences" rules.
6. **CLI** — register a new subcommand under `audit` in `apps/cli/main.py`.
7. **Fixture + test** — add a `tests/fixtures/<domain>_*/` repo and a `tests/code_audit/test_<domain>_flow.py` with at least: an adapter-level test, an end-to-end test asserting verdict distribution, and a trust-gate test asserting that a hallucinating checker stub is overridden.
8. **Docs** — update README's "Code audit" section and `ROADMAP.md`. Add a `CHANGELOG.md` entry under `[Unreleased]`.

## Things that look like good ideas but aren't

- **Letting the LLM verdict without evidence.** No matter how good the model, the trust gate stays. The whole product premise rides on this.
- **Premature shared base class for orchestrators.** Wait until the *third* auditor lands before extracting `BaseAuditFlow`. Two examples isn't a pattern; three is.
- **Sharing `core/schemas.py` between domains.** Keep `schemas_audit.py` separate. The merge cost is low; the divergence cost of cross-domain field coupling is high.
- **Adding the README to its own evidence pool.** Already burned us once. Self-evidence makes hallucinations verify themselves.
- **Skipping `from __future__ import annotations` because "we're on 3.13 locally".** CI runs on 3.9.
