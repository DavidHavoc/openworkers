# Examples

## Research session

Full pipeline: literature search, classification, citation audit, critique.

```bash
$ thesis research "Does social media use increase anxiety in adolescents?" \
    --discipline psychology \
    --summary "Exploring causal link between screen time and clinical anxiety scores."

============================================================
RESEARCH SESSION: a1b2c3d4-...
Status: complete | Created: 2026-05-04T10:00:00Z
============================================================

Research Question: Does social media use increase anxiety in adolescents?
Discipline: psychology
Topic: Exploring causal link between screen time and clinical anxiety scores.

--- Research Plan ---
  Plan ID: plan-001
  Strategy: broad_survey
  Subquestions:
    - What mechanisms link social media to anxiety?
    - What longitudinal studies exist?

=== LITERATURE MAP ===
Research Question: Does social media use increase anxiety in adolescents?
Total Found: 12 | Query: social media anxiety adolescents

--- Supporting ---
  1. [2023] A longitudinal study of social media and adolescent anxiety
     ID: 10.1016/j.adolescence.2023.05.001
  2. [2022] Social comparison mediates the link between Instagram use and anxiety
     ID: 2301.01234

--- Challenging ---
  1. [2021] No robust association found between total screen time and anxiety
     ID: 10.1038/s41598-021-00001

--- Adjacent ---
  1. [2024] Digital wellbeing interventions for teens: a meta-analysis
     ID: 10.1146/annurev-psych-2024-01

=== CITATION AUDIT ===
Claims Checked: 8 | Verified: 6

Missing Citations (1):
  - "social media causes anxiety" — no source for causal claim

Weak Citations (1):
  - Source 10.1016/socialmedia.2019 exists but reports screen time,
    not clinical anxiety scores

=== SYNTHESIS REPORT ===
Research Question: Does social media use increase anxiety in adolescents?

## Methods
  common_methods:
    - longitudinal survey (4 papers)
    - ecological momentary assessment (2 papers)
  methodological_gaps:
    - No RCTs found in this space

## Datasets
  datasets_used:
    - AddHealth
    - UK Millennium Cohort Study

## Corpus Insights
  consensus_findings:
    - Screen time correlates with anxiety at r=0.15-0.25
  knowledge_gaps:
    - Mechanism is unclear — social comparison vs. sleep displacement

=== CRITIQUE ===

## Strengths
  - Timely and relevant research question
  - Feasible with existing survey datasets

## Weaknesses
  - Causal direction is hard to establish cross-sectionally
  - Self-report measures for both screen time and anxiety

## Gaps
  - No studies that experimentally reduce social media
  - No studies controlling for offline social interaction

## Counterarguments
  - Orben 2019 found effect sizes too small to be meaningful
  - Twenge 2020 was criticized for selective time-window analysis

## Suggestions
  - Narrow to a specific mechanism (passive use, comparison frequency)
  - Use longitudinal or EMA design instead of cross-sectional

Overall: The question is viable but needs narrowing. Biggest risk is confounding
with offline social behavior and reverse causation.
```

## Critique an idea

```bash
$ thesis critique "Social media causes depression because teens spend too much time online." \
    --discipline psychology

=== CRITIQUE ===

## Strengths
  - Identifies a plausible correlation

## Weaknesses
  - Assumes causation from correlation
  - No mechanism specified (sleep? comparison? displacement?)
  - "too much time" is undefined

## Gaps
  - No operational definition of "depression"
  - Missing longitudinal evidence
  - Does not address bidirectional effects

## Counterarguments
  - Odgers 2020 argues digital technology effects are overblown
  - Some studies find positive effects of online social connection

## Suggestions
  - Specify mechanism: passive browsing vs active interaction
  - Define depression measure (PHQ-9, CES-D, clinical diagnosis)
  - Consider moderators: age, gender, pre-existing mental health

Overall: The claim is too broad to be testable. Narrow to a specific platform,
mechanism, and outcome measure.
```

## Verify a citation

```bash
$ thesis verify "10.1038/nature14539"

DOI EXISTS: Deep learning
  Year: 2015 | Publisher: Springer Science and Business Media LLC
  Authors: Yann LeCun, Yoshua Bengio, Geoffrey Hinton
```

Fake DOI:

```bash
$ thesis verify "10.9999/does-not-exist"

DOI NOT FOUND (does not exist)
```

## Quick paper search

No LLM involved — pure API call.

```bash
$ thesis papers "transformer attention" --source arxiv --limit 3

Papers for: 'transformer attention' (source=arxiv, limit=3)
Found: 3
  1. [2024] Attention Guided CAM: Visual Explanations of Vision Transformer
     ID: 2402.04563v1
  2. [2023] Mask-Attention-Free Transformer for 3D Instance Segmentation
     ID: 2309.01692v1
  3. [2022] Dilated Neighborhood Attention Transformer
     ID: 2209.15001v3
```

Set `--source semantic_scholar` for DOI-verified results with citation counts.

## JSON output (for piping)

```bash
$ thesis research "Does X cause Y?" --format json --output result.json

$ cat result.json | jq '.status'
"complete"

$ cat result.json | jq '.lit_map.total_found'
12

$ cat result.json | jq '.critique.overall_assessment'
"The question is viable but needs narrowing. Biggest risk is confounding."
```

## Ingest a thesis into the corpus

```bash
$ thesis corpus ingest "thesis.pdf" \
    --title "Deep Learning for Medical Image Segmentation" \
    --discipline computer_science \
    --university "Stanford" \
    --year 2024

Ingested: thesis.pdf
  Discipline: computer_science
  Sections: 8
  Types: ['introduction', 'literature_review', 'methodology', 'results', 'discussion', 'conclusion']
```

## Eval harness

Run all 7 evaluation tests:

```bash
$ python -m core.evals.thesis_harness

============================================================
THESIS EVALUATION HARNESS
============================================================
  [PASS] Search Recall (crossref_verification)
  [PASS] Structure Check (ResearchSession fields)
  [PASS] Fake DOI Detection
  [PASS] Bad Idea Detection (too-broad critique)
  [PASS] Cost Measurement (3 sessions)
  [PASS] Synthesis Quality (structure check)
  [PASS] Full Pipeline Integrity (all 8 stages)

============================================================
SCORECARD: 7/7 passed
============================================================
OVERALL: PASS
```
