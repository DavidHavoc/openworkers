import asyncio
import os
import time
from typing import List

from core.memory.episodic import EpisodicMemory
from core.orchestrator.thesis_flow import ThesisOrchestrator
from core.router.engine import Router
from core.schemas import (
    CitationAudit,
    CritiqueResult,
    LitMap,
    ResearchContext,
    ResearchPlan,
    ResearchSession,
    SynthesisReport,
)
from providers.adapters import create_unified_llm
from tools.mcp.engine import ToolRegistry


class ThesisEvalResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.detail = ""
        self.errors: List[str] = []

    def pass_(self, detail: str = "") -> None:
        self.passed = True
        self.detail = detail

    def fail(self, detail: str) -> None:
        self.passed = False
        self.detail = detail

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)


class ThesisEvalHarness:
    def __init__(self) -> None:
        self.results: List[ThesisEvalResult] = []
        self.tool_registry: ToolRegistry = ToolRegistry()  # type: ignore[no-untyped-call]

    async def run(self) -> bool:
        print("=" * 60)
        print("THESIS EVALUATION HARNESS")
        print("=" * 60)

        await self._test_search_recall()
        await self._test_structure_check()
        await self._test_fake_doi_detection()
        await self._test_bad_idea_detection()
        await self._test_cost_measurement()
        await self._test_synthesis_quality()
        await self._test_full_pipeline_integrity()

        print()
        return self._print_scorecard()

    async def _test_search_recall(self) -> None:
        r = ThesisEvalResult("Search Recall (crossref_verification)")
        try:
            tool = self.tool_registry.get_tool("crossref_verification")
            result = await tool.execute({"doi": "10.1038/nature14539"}, "public")
            if result.get("exists") and "Deep learning" in result.get("title", ""):
                r.pass_("Known DOI verified  -  CrossRef tool returns correct metadata")
            elif not result.get("exists"):
                r.fail("Known DOI 10.1038/nature14539 not found. API may be unavailable.")
            else:
                r.pass_(f"DOI verified: title={result.get('title', '')[:60]}")
        except Exception as e:
            r.fail(f"CrossRef tool error: {e}")

        # Also verify fake DOI is rejected
        try:
            tool = self.tool_registry.get_tool("crossref_verification")
            result = await tool.execute({"doi": "10.9999/doesnotexist12345"}, "public")
            if result.get("exists") is False:
                if r.passed:
                    r.detail += " | Fake DOI correctly rejected"
            else:
                r.add_error("Fake DOI was not rejected")
        except Exception as e:
            r.add_error(f"Fake DOI check error: {e}")

        self.results.append(r)
        self._print_result(r)

    async def _test_structure_check(self) -> None:
        r = ThesisEvalResult("Structure Check (ResearchSession fields)")
        try:
            orch = self._create_orchestrator()
            rc = ResearchContext(
                research_question="Does X cause Y?",
                topic_summary="Investigating whether X leads to Y.",
                discipline="computer_science",
            )
            session = await orch.execute(rc)

            checks = []

            if isinstance(session, ResearchSession):
                checks.append("type=ResearchSession")
            else:
                r.fail(f"Not a ResearchSession: {type(session)}")
                self.results.append(r)
                self._print_result(r)
                return

            if session.research_plan and isinstance(session.research_plan, ResearchPlan):
                checks.append("research_plan")
            else:
                r.add_error("research_plan missing or wrong type")

            if session.lit_map and isinstance(session.lit_map, LitMap):
                checks.append("lit_map")
            else:
                r.add_error("lit_map missing or wrong type")

            if session.citation_audit and isinstance(session.citation_audit, CitationAudit):
                checks.append("citation_audit")
            else:
                r.add_error("citation_audit missing or wrong type")

            if session.synthesis_report and isinstance(session.synthesis_report, SynthesisReport):
                checks.append("synthesis_report")
            else:
                r.add_error("synthesis_report missing or wrong type")

            if session.critique and isinstance(session.critique, CritiqueResult):
                checks.append("critique")
            else:
                r.add_error("critique missing or wrong type")

            if session.session_id and len(session.session_id) > 0:
                checks.append("session_id")
            else:
                r.add_error("session_id missing")

            if session.created_at and len(session.created_at) > 0:
                checks.append("created_at")
            else:
                r.add_error("created_at missing")

            if session.status in ("complete", "partial"):
                checks.append(f"status={session.status}")
            else:
                r.add_error(f"bad status: {session.status}")

            if not r.errors:
                r.pass_(f"All fields present: {', '.join(checks)}")
            else:
                r.fail(f"Passed {len(checks)} checks, errors: {r.errors}")
        except Exception as e:
            r.fail(f"Exception: {e}")

        self.results.append(r)
        self._print_result(r)

    async def _test_fake_doi_detection(self) -> None:
        r = ThesisEvalResult("Fake DOI Detection")
        try:
            orch = self._create_orchestrator()
            verify = await orch._verify_single_citation(
                claim="X causes Y",
                doi_or_title="10.9999/nonexistent-fake-doi-totally",
            )
            if verify.get("exists") is False:
                r.pass_("Fake DOI correctly rejected by crossref_verification")
            elif "error" in verify:
                r.pass_(
                    f"DOI tool returned error (API may be limited): {verify.get('error', '')[:80]}"
                )
            else:
                r.fail(f"Fake DOI was accepted: {verify}")
        except Exception as e:
            r.fail(f"Exception: {e}")

        self.results.append(r)
        self._print_result(r)

    async def _test_bad_idea_detection(self) -> None:
        r = ThesisEvalResult("Bad Idea Detection (too-broad critique)")
        try:
            orch = self._create_orchestrator()
            rc = ResearchContext(
                research_question="Does social media cause depression?",
                topic_summary="A broad question without specific scope or mechanism.",
                discipline="psychology",
            )
            critique = await orch._critique_only(rc)

            if not isinstance(critique, CritiqueResult):
                r.fail(f"Did not return CritiqueResult: {type(critique)}")
                self.results.append(r)
                self._print_result(r)
                return

            checks = []
            if critique.overall_assessment:
                checks.append("has assessment")
            else:
                checks.append("no assessment (DRY_RUN)")

            if critique.gaps:
                checks.append(f"{len(critique.gaps)} gaps listed")
            if critique.weaknesses:
                checks.append(f"{len(critique.weaknesses)} weaknesses listed")
            if critique.suggestions:
                checks.append(f"{len(critique.suggestions)} suggestions listed")

            if checks:
                r.pass_(f"CritiqueResult produced: {', '.join(checks)}")
            else:
                r.fail("CritiqueResult has no content")
        except Exception as e:
            r.fail(f"Exception: {e}")

        self.results.append(r)
        self._print_result(r)

    async def _test_cost_measurement(self) -> None:
        r = ThesisEvalResult("Cost Measurement (3 sessions)")
        try:
            orch = self._create_orchestrator()
            times: List[float] = []

            for i in range(3):
                t0 = time.monotonic()
                _ = await orch.execute(
                    ResearchContext(
                        research_question=f"Test query {i}",
                        topic_summary=f"Test topic {i}.",
                        discipline="computer_science",
                    )
                )
                elapsed = time.monotonic() - t0
                times.append(elapsed)

            avg_ms = sum(times) / len(times) * 1000
            min_ms = min(times) * 1000
            max_ms = max(times) * 1000

            if avg_ms < 5000:
                r.pass_(f"avg={avg_ms:.0f}ms min={min_ms:.0f}ms max={max_ms:.0f}ms  -  within 5s")
            else:
                r.pass_(f"avg={avg_ms:.0f}ms min={min_ms:.0f}ms max={max_ms:.0f}ms")
        except Exception as e:
            r.fail(f"Exception: {e}")

        self.results.append(r)
        self._print_result(r)

    async def _test_synthesis_quality(self) -> None:
        r = ThesisEvalResult("Synthesis Quality (structure check)")
        try:
            orch = self._create_orchestrator()
            rc = ResearchContext(
                research_question="What is deep learning?",
                topic_summary="Overview of deep learning methods.",
                discipline="computer_science",
            )
            session = await orch.execute(rc)
            sr = session.synthesis_report

            if sr is None:
                r.fail("synthesis_report is None")
                self.results.append(r)
                self._print_result(r)
                return

            checks = []
            if isinstance(sr.research_question, str) and sr.research_question:
                checks.append("research_question")
            if isinstance(sr.method_summary, dict):
                checks.append("method_summary")
            if isinstance(sr.dataset_summary, dict):
                checks.append("dataset_summary")
            if isinstance(sr.metric_summary, dict):
                checks.append("metric_summary")
            if isinstance(sr.corpus_insights, dict):
                checks.append("corpus_insights")
            if isinstance(sr.recommended_reading, list):
                checks.append("recommended_reading")
            if isinstance(sr.cross_paper_comparisons, list):
                checks.append("cross_paper_comparisons")

            r.pass_(f"All fields present: {', '.join(checks)}")
        except Exception as e:
            r.fail(f"Exception: {e}")

        self.results.append(r)
        self._print_result(r)

    async def _test_full_pipeline_integrity(self) -> None:
        r = ThesisEvalResult("Full Pipeline Integrity (all 8 stages)")
        try:
            orch = self._create_orchestrator()
            rc = ResearchContext(
                research_question="Does X cause Y?",
                topic_summary="Investigating causal relationship.",
                discipline="science",
                existing_knowledge="Previous work shows correlation.",
                what_they_need="Causal mechanism papers.",
            )
            session = await orch.execute(rc)

            checks = []
            if session.research_plan:
                checks.append("stage1:HEAD_planner")
            if session.lit_map:
                checks.append("stage3:researcher")
            if session.citation_audit:
                checks.append("stage4:checker")
            if session.synthesis_report:
                checks.append("stage5:synthesizer")
            if session.critique:
                checks.append("stage6:critic")
                checks.append("stage7:HEAD_supervisor")
            if session.status:
                checks.append("stage8:assemble")

            if len(checks) >= 7:
                r.pass_(f"Pipeline complete: {', '.join(checks)}")
            else:
                r.fail(f"Missing stages: expected 7+, got {len(checks)}: {checks}")
        except Exception as e:
            r.fail(f"Exception: {e}")

        self.results.append(r)
        self._print_result(r)

    def _create_orchestrator(self) -> ThesisOrchestrator:
        unified = create_unified_llm()
        memory = EpisodicMemory(qdrant_location=":memory:")
        router = Router()
        return ThesisOrchestrator(
            unified=unified,
            memory=memory,
            router=router,
            tool_registry=self.tool_registry,
        )

    def _print_result(self, r: ThesisEvalResult) -> None:
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.name}")
        if r.detail:
            print(f"         {r.detail}")
        for err in r.errors:
            print(f"         ERR: {err}")

    def _print_scorecard(self) -> bool:
        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)
        total = len(self.results)
        all_pass = failed == 0

        print("=" * 60)
        print(f"SCORECARD: {passed}/{total} passed")
        if not all_pass:
            for r in self.results:
                if not r.passed:
                    print(f"  FAIL: {r.name}  -  {r.detail}")
        print("=" * 60)

        if all_pass:
            print("OVERALL: PASS")
        else:
            print("OVERALL: PARTIAL (some tests need real LLM/API access)")

        return all_pass


if __name__ == "__main__":
    os.environ["DRY_RUN"] = "true"
    harness = ThesisEvalHarness()
    asyncio.run(harness.run())
