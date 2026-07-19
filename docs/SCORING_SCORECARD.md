# Taali scorecard — one canonical vocabulary

**Date:** 2026-06-26 · **Status:** canonical (supersedes the 6-axis "fluency" radar and the 8 "canonical dimensions").

There is **one** scorecard, used everywhere (candidate report, radar, marketing, glossary, docs): the **4 Ds + Deliverable** — 5 dimensions, anchored on Anthropic's published AI-Fluency framework.

| # | Dimension | What it measures |
|---|-----------|------------------|
| 1 | **Delegation** | Deciding what to own vs. hand to the agent, and steering the load-bearing design calls. |
| 2 | **Description** | Directing the agent and communicating clearly — the prompts, the context provided, and the write-up. |
| 3 | **Discernment** | Critically evaluating the agent's output — catching and overriding what's wrong. |
| 4 | **Diligence** | Verifying before claiming done, and owning the shipped result and its residual risk. |
| 5 | **Deliverable** | Correctness and quality of what was actually shipped. |

(Communication folds into **Description**; engineering-quality — code craft, systems design, release safety — folds into **Deliverable** + its evidence.)

## Approach: one scorecard, everything else is evidence
- **The per-task rubric stays the authoritative grade.** Each rubric dimension is tagged to exactly one of the 5 axes (`fluency` tag; default derived from its grader/lens — see `rubric_scoring.fluency_axis_for_dimension`). The weighted rubric score remains `assessment_score`.
- Rubric dimensions roll up to the 5 axes (`rubric_scoring.summarize_fluency_4d` → `score_breakdown.rubric_grading.fluency_4d`).
- **The 5 axes are the only top-level scorecard shown.** The per-task rubric dimensions and the ~30 heuristic metrics become the **evidence drill-down** *under* each axis — not competing scorecards.

## Single source of truth (frontend)
`frontend/src/shared/assessment/fluency4d.js`:
- `FLUENCY_4D_AXES` — the 5 axes `{ key, label, blurb, sources }` (`sources` = the heuristic atomic `*_score` columns surfaced as **telemetry** under the axis).
- `computeScorecard(assessment)` — returns the 5 axes, each scored from the **graded rubric only** (`fluency_4d[axis]`), else `null` ("not assessed"). Heuristic columns come back separately as `telemetry` and are never a score. This is THE function every report surface uses.

### Heuristic → axis telemetry map (evidence, NOT a score)

**Changed 2026-07-19.** These columns used to backfill an axis with no rubric grade, so the report rendered a full five-axis scorecard even when only two axes were graded. Several columns are aliases of one prompt-word-count formula (`prompt_quality_score`, `design_thinking_score`, `written_communication_score`, `learning_velocity_score`, `requirement_comprehension_score` are all the same value; `submission_runtime` hardcodes `code_quality_score = 5.0`), so the backfilled number looked graded but measured almost nothing. They are now shown only as labelled telemetry under an ungraded axis, and the axis itself reads "—". Every production task grades all five axes (CI-gated), so telemetry surfaces only on assessments scored before that landed, or on an off-catalog task.

| Axis | Atomic `*_score` columns |
|------|--------------------------|
| Delegation | `design_thinking_score`, `requirement_comprehension_score` |
| Description | `prompt_quality_score`, `context_utilization_score`, `written_communication_score` |
| Discernment | `debugging_strategy_score`, `learning_velocity_score` |
| Diligence | `error_recovery_score`, `independence_score`, `prompt_efficiency_score`, `time_efficiency_score` |
| Deliverable | *(none — `code_quality_score` is a hardcoded constant, deliberately not surfaced)* |

## What this retires
- `fluencyRollup.js`'s 6 axes (Systems design / Code craft / Reasoning / AI collaboration / Release safety / Communication) as a top-level scorecard.
- `scoringDimensions.ts`'s 8 "canonical dimensions" as a top-level scorecard (its `normalizeScores` may remain only if still needed to bucket legacy data into the evidence view).
- Any "5 / 6 / 8 dimensions" language in report copy, marketing, and docs → **5 dimensions (the 4 Ds + Deliverable)**, consistently.
