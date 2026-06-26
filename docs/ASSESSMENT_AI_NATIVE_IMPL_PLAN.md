# AI-Native Assessment Overhaul — Implementation Plan (Option A)

**Date:** 2026-06-26
**Decision locked:** Option A — keep the standardised, instrumented in-platform harness (the moat: consistent, automatable, defensible scoring); invest in process-visibility, scoring the verification half, and re-basing on Anthropic's AI Fluency 4 Ds. Companion: [`ASSESSMENT_AI_NATIVE_REVIEW.md`](./ASSESSMENT_AI_NATIVE_REVIEW.md).
**Status (2026-06-26):** Tier 0 + Tier 1 foundation LANDED on `claude/wizardly-meitner-52f336` (all behind flags / additive / gated — zero live-score change until shadow-validated). 140 backend tests + 6 vitest green.

| PR | What | Status |
|----|------|--------|
| PR-1 | Capture agent tool results onto `ai_prompts` | ✅ landed |
| PR-2 | Feed trace + git diff to grader (`ASSESSMENT_GRADER_PROCESS_TRACE`, default off) | ✅ landed (flag off) |
| PR-3 | Drop the artificial 3-tool-call cap | ✅ landed |
| PR-4 | EKS/AKS outliers → catalog standard (decision_points + interrogation + lenses) | ✅ landed |
| PR-A | Shadow re-score harness | ✅ landed |
| PR-5 | Discernment + Diligence grader lenses (machinery) | ✅ landed (inert until a task adopts them) |
| PR-6 | 4 Ds rollup (`fluency_4d`), additive/derived | ✅ landed |
| PR-7 | Surface the 4 Ds on the candidate report | ✅ landed (needs live authed-page verify) |
| PR-8 | "Promote Description" + stale-wiring fix | ⏸️ stale-key is a harmless no-op on soon-superseded telemetry — skipped; Description-as-graded-dim folds into the gated task-enrichment step |
| PR-9 | Planted-trap discernment grading (machinery) | ✅ landed (inert until a task declares `traps`) |
| PR-10 | Per-task `agent_model` override | ✅ landed (default Haiku unchanged — no cost surprise) |
| PR-11 | Plan-before-build nudge | ✅ landed. Controlled lookup tool ⛔ **deferred** (integrity: sandbox network is blocked by design — a curated lookup source needs explicit design) |
| PR-12 | Async "explain & defend" | ⛔ **deferred** — needs the live assessment-runtime UI loop to author + verify |

**The gated flips (need a PR-A shadow run on prod data + Sam's go, NOT done autonomously):** (1) turn `ASSESSMENT_GRADER_PROCESS_TRACE` on in prod; (2) add weighted Discernment/Diligence dimensions (and `traps`) to flagship tasks — this is what actually populates those 4 Ds axes + scores the verification half. Until then the rollup honestly shows Delegation + Deliverable.

**Deploy note:** task specs auto-sync from the JSON files into the DB **once per worker process, on the first `GET /tasks/` after a restart** (`_sync_template_task_specs_if_needed`, module-guarded; skipped for sqlite). So on deploy:
- **Goes live automatically** (after first `/tasks/` load): **PR-4 EKS/AKS standardisation** — those two tasks switch to the interrogation opener + 6-dim rubric (their `technical_design`/`implementation_quality` move to the deliverable lens). This is the one **non-flag-gated scoring change**; it's a correctness alignment to the other 8 tasks and the two are ~zero-traffic, but flag it for sign-off.
- **Live immediately:** PR-3 (tool-call cap removed) + PR-11 (plan nudge) in the candidate chat; the additive `fluency_4d` rollup on new gradings.
- **Stays off:** PR-2 trace flag, PR-5 discernment/diligence lenses + PR-9 `traps` (inert until a task adopts them), PR-10 model override (no task sets it). The score-/cost flips remain gated on a PR-A shadow run + Sam's go.

---

## Target architecture (end state)

1. **One authoritative scorer = `RubricScorer`**, fed the **full process trace** (candidate messages + agent text + tool calls **+ tool results** + git diff + test results), not just message/response text.
2. **Every rubric dimension is tagged with a fluency axis** ∈ `{delegation, description, discernment, diligence, deliverable}`. Dimension grades roll up to these **5 axes** (Anthropic's 4 Ds + a Deliverable/outcome axis) for presentation.
3. **Verification & oversight are scored**, not just decision-ownership: new `discernment` and `diligence` grader lenses + an optional planted-trap mechanic (does the candidate catch a wrong-but-plausible AI suggestion / latent bug?).
4. **Heuristics are demoted to telemetry** (kept for the recruiter "how they worked" tab, no longer driving the headline number). The dead-ended LLM `analyze_prompt_session` path is retired.
5. **Harness realism** (Tier 2): stronger/selectable model; relaxed tool-call cap; plan-before-build affordance; controlled doc lookup. Interrogation classifier (Haiku) + rubric grader (Sonnet 4.5) stay on **fixed** models for scoring consistency.

**Guiding constraints (from memory/house rules):**
- Any change to the **authoritative score must be shadow-validated** before flip (the metering/policy cutovers were blocked precisely for lacking this). → PR-A below.
- **Verify on real authed candidate/recruiter pages**, not the showcase (different components).
- Test locally (vitest + pytest isolated + arch gate) before pushing; backend suite is flaky in-batch → run suites in isolation.
- Prod deploy = commit→push→PR→merge to `main` (Git auto-deploy; migrations auto-run on boot). Watch alembic multi-head if landing migrations alongside other PRs.

---

## Migration footprint — minimal

| Change | Storage | Migration? |
|---|---|---|
| Tool results | enrich `ai_prompts` JSON record | **No** (JSON column) |
| Git diff → grader | already on `assessment.git_evidence` JSON | **No** |
| New grader lenses / outputs | `score_breakdown.rubric_grading` JSON | **No** |
| 4D rollup scores | `score_breakdown.fluency_4d` JSON | **No** |
| Planted-trap outcome | `score_breakdown` + task spec JSON | **No** |
| *Optional* atomic 4D `*_score` columns (only if we want them indexed/queryable) | `assessments` table | **Yes** — 1 migration, single head, defer until JSON proven |

**Tier 0 + Tier 1 ship with zero migrations** by living in existing JSON columns. Atomic columns are an optional later optimisation.

---

## PR breakdown

### Tier 0 — Make the process visible (no migrations, low risk)

**PR-1 · Capture agent tool *results* (not just calls).**
- `claude_agent/service.py:317-325` — the stream loop handles only `AssistantMessage`/`ResultMessage`; the SDK delivers tool results as a separate message (`UserMessage` carrying `ToolResultBlock` content). Handle that type; correlate result→call by `tool_use_id`; bound each result (~1–2k chars).
  - ⚠️ *Implementation note:* confirm the exact SDK message/block class names against the installed `claude-agent-sdk` version before coding (types vary by version).
- `claude_agent/types.py:40` — change `tool_calls_made` entries from `{name, input}` to `{name, input, result, is_error, tool_use_id}` (or add a parallel `tool_results` list). Update the docstring (currently says "analytics-only … {name, input}").
- `candidate_claude_chat_routes.py:382` — record already passes `tool_calls_made` through verbatim; no shape change needed once `ChatTurn` carries results. JSON column → **no migration**.
- Tests: unit-test the stream handler with a synthetic `AssistantMessage(ToolUseBlock) → UserMessage(ToolResultBlock) → ResultMessage` sequence; assert results captured + bounded.

**PR-2 · Feed the trace + git diff to the grader.** *(changes scores → ships behind flag, shadow-validated via PR-A)*
- `rubric_scoring.py:133-142` — extend `prompt_transcript_excerpt` to render, per turn, the interleaved `[Candidate] → [tools: Read x, Bash → exit 0, Edit applied] → [Claude]` trace (bounded). Keep message/response; add a compact tool line.
- `rubric_scoring.py:71-109` — add `git_evidence: Dict` field to `ScoringArtifacts` + a `git_evidence_excerpt()` (diff/commits, bounded ~6k chars).
- `rubric_scoring.py:176-227` — update `_GRADER_PREAMBLE` + the DECISION/DELIVERABLE lens prompts to reference "the candidate's tool actions and the git diff" as gradable evidence.
- `submission_runtime.py:752-760` — pass `git_evidence=assessment.git_evidence` into `ScoringArtifacts` (already captured at submit).
- Flag: `ASSESSMENT_GRADER_PROCESS_TRACE` (default off) — when off, grader behaves exactly as today.
- Tests: snapshot the new excerpt; grader-input contains tool results + diff when flag on.

**PR-3 · Relax the artificial tool-call cap.** *(prompt-text only, near-zero risk)*
- `candidate_claude_chat_routes.py:91-95` — remove the "NO MORE than 3 tool calls / 4th IS a failure" block (it's **prompt-only** — confirmed there is no runtime enforcement; the real bound is `CLAUDE_TOOL_MAX_TURNS=25`). Replace with step/verify guidance: "work in focused steps; verify edits by running tests; you have a per-turn budget and 30 minutes."
- Optional: expose `CLAUDE_TOOL_MAX_TURNS` per-task if some tasks need more headroom.
- Tests: prompt-builder snapshot; manual latency/cost check on one live run (monitor per-turn USD against the existing `$1/turn`, `$5/assessment` caps).

**PR-4 · Fix the EKS/AKS outlier tasks.**
- `backend/tasks/platform_eng_aws_eks_misconfig_triage.json` + `…_azure_aks_….json` — add a `decision_points` array + per-dimension `lens` + a `design_decisions_articulated` interrogation dimension; rebalance weights to sum 1.0 within the 4–6 dim limit (`task_spec_loader.py:472`). Use `data_eng_data_quality_contract_framework.json` as the template (it has the canonical `decision_points` + lens shape).
- Reseed: `scripts/seed_tasks_db.py`.
- Tests: `validate_task_spec` passes for both; weight-sum + decision-dim validators green.

### Tier 1 — Score the verification half + 4D rebase

**PR-A · Shadow-scoring harness (tooling; prerequisite for any score-flip).**
- Script `scripts/shadow_rescore_assessments.py` — re-grade a sample of historical completed assessments under a given flag config (old vs PR-2/PR-5/PR-6) and emit a comparison (per-dimension + overall delta distribution, rank-correlation, band-flip count).
- Run before flipping each scoring PR; gate the flip on "no pathological re-ranking."
- No prod write; reads `ai_prompts`/`git_evidence`/`final_repo_state` from prod replica.

**PR-5 · Discernment & Diligence grader lenses.** *(depends on PR-2; flag + shadow)*
- `rubric_scoring.py` — add `_DISCERNMENT_LENS_PROMPT` (did the candidate evaluate/verify the agent's output, catch & reject a wrong suggestion, test before trusting?) and `_DILIGENCE_LENS_PROMPT` (did they run tests, own residual risk, ship a verified result?). Wire into `_system_prompt_for_lens` (234-237). These only have teeth because PR-2 made the trace visible.
- Task JSONs — add `discernment`/`diligence` dimensions to 2–3 flagship tasks first (data_quality, genai_production_readiness, glue_recovery), rebalancing weights.

**PR-6 · 4D tagging + rollup (backend).** *(flag + shadow)*
- Task JSONs — add `fluency: "delegation|description|discernment|diligence|deliverable"` to each rubric dimension. Map: interrogation/decision dims → `delegation`; prompt/context-quality → `description`; verification/catch-the-error → `discernment`; testing/ownership → `diligence`; correctness lenses → `deliverable`.
- `task_spec_loader.py:455-506` — validate the `fluency` tag (enum) on each dimension.
- `rubric_scoring.py` / `submission_runtime.py:775-793` — roll dimension grades up to the 5 axes; persist `score_breakdown.fluency_4d = {delegation, description, discernment, diligence, deliverable}` (weighted means). **No migration** (JSON).

**PR-7 · 4D presentation (frontend).** *(flag until parity verified on real authed pages)*
- `scoring/scoringDimensions.ts` — add 5 canonical ids (the 4 Ds + deliverable); fold legacy keys in via `legacyAliases` so `normalizeScores` remaps old data automatically.
- `shared/assessment/fluencyRollup.js` — source the axes from `score_breakdown.fluency_4d` when present; fall back to the current 6-axis computation for pre-rebase assessments. (Also fix the `error_recovery_score` double-count, lines 29/39.)
- `features/candidates/CandidateStandingReportPage.jsx:1553-1641` + sibling views (`CandidateReportView`, `CandidateResultsPreviewView`, `CandidateAssessmentSummaryView`, `CandidateScoreSummarySheet`, `demo/demoSummary.js`) + `lib/scoringGlossary.ts` — relabel to the 4 Ds + Deliverable, with tooltips citing Anthropic's AI Fluency framework.
- Marketing copy (`LandingPageContent.jsx`, `DemoShowcasePage.jsx`, `RouteMeta.jsx`) — update the rubric description to "assessed on Anthropic's AI Fluency framework (Delegation · Description · Discernment · Diligence)" — a credibility asset.

**PR-8 · Promote Description + fix stale wiring.**
- Make prompt/context-engineering quality a *graded* `description` dimension (via PR-5/6 lens) rather than the dead-ended heuristic/LLM path.
- Fix `submission_runtime.py:871` — reads `category_scores["efficiency"]` but the engine emits `"independence"` (no `"efficiency"` key) → `prompt_efficiency_score` silently falls to a default. Either correct the key or retire the column as the radar moves to `fluency_4d`.
- Retire the dead `claude.analyze_prompt_session` path (computed, returned as `prompt_scores`, but never drives persisted columns) to cut cost + confusion.

**PR-9 · Planted-trap mechanic (highest-signal Discernment; hardest to game).**
- Task spec — add optional `traps` array (a wrong-but-plausible suggestion seeded in the repo/likely agent path + detection criteria), validated in `task_spec_loader.py`.
- `rubric_scoring.py` — a `trap_outcome` grader (sibling to `interrogation_outcome`) scoring whether the candidate caught/rejected it (direct RSR "appropriate reliance" test).
- Start with 1–2 tasks; can slip to a Tier-1.5 if Tier 1 is getting heavy.

### Tier 2 — Harness realism (the strategic upgrades)

**PR-10 · Model upgrade / candidate choice.** `service.py:73` — bump `_DEFAULT_AGENT_SDK_MODEL` (or surface a per-assessment model pick + score the *choice* as a Delegation/Platform-Awareness signal). Keep classifier + grader fixed. Re-check latency budget.

**PR-11 · Plan-before-build + controlled lookup.** Add a captured "plan" step graded under Delegation, and a bounded doc-lookup tool (real engineers look things up) scored on use. Capture both in the trace.

**PR-12 · (optional) Async "explain & defend".** Short post-submit written/recorded reflection graded under Discernment/Diligence — the market's consensus human-defense step, without breaking automation.

---

## Sequencing & dependencies

```
Tier 0:  PR-1 ─┬─ PR-2 ──(flag, shadow via PR-A)──► flip
               └─ PR-3 (independent)   PR-4 (independent)
Tier 1:  PR-A ─► PR-5 ─► PR-6 ─► PR-7 ─► PR-8 ;  PR-9 (after PR-2)
Tier 2:  PR-10 (independent) ;  PR-11 ;  PR-12
```
- **PR-1 → PR-2** is the critical path: results must be captured before the grader can read them.
- **PR-A precedes every score-flip** (PR-2, PR-5, PR-6 flips).
- PR-3, PR-4, PR-10 are independent quick wins, mergeable anytime.

## Rollout pattern (per score-changing PR)
1. Land behind flag (default off) → 2. shadow re-grade historical sample (PR-A) → 3. live shadow on new assessments (grade both ways, log delta) → 4. review distribution/re-ranking with Sam → 5. flip flag → 6. verify on a real authed candidate report page.

## Risk register
- **Grader cost/latency up** (trace is bigger): bounded excerpts; Sonnet grader is per-dimension already; monitor UsageEvents.
- **SDK message-type names** (PR-1): version-specific — confirm before coding.
- **Score drift** on rebase: mitigated by PR-A shadow gate.
- **Three-vocabulary reconciliation** (PR-7): `legacyAliases`/`normalizeScores` give a clean remap; old assessments keep rendering via fallback.
- **Alembic multi-head**: only if optional atomic columns land — keep to one migration, check heads.

## Effort (rough)
- Tier 0: ~2–4 days (PR-1/2 the bulk; PR-3/4 hours).
- Tier 1: ~1.5–2.5 weeks (PR-7 FE + PR-9 trap the heaviest).
- Tier 2: ~1–3 weeks depending on PR-11/12 scope.
