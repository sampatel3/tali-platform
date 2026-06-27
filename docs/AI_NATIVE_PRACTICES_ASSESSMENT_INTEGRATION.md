# Assessing AI-Native *Practice Proficiency* — Integration Design

**Date:** 2026-06-27
**Status:** Design (proposal — additive, flag-gated, shadow-validated before any score flip)
**Companions:** [`AI_NATIVE_BEST_PRACTICES.md`](./AI_NATIVE_BEST_PRACTICES.md) (what the practices are) ·
[`ASSESSMENT_AI_NATIVE_REVIEW.md`](./ASSESSMENT_AI_NATIVE_REVIEW.md) +
[`ASSESSMENT_AI_NATIVE_IMPL_PLAN.md`](./ASSESSMENT_AI_NATIVE_IMPL_PLAN.md) (the process-visibility + 4 Ds rebase
this builds on) · [`SCORING_SCORECARD.md`](./SCORING_SCORECARD.md) · [`TAALI_SCORING_RUBRIC.md`](./TAALI_SCORING_RUBRIC.md) ·
[`NORTH_STAR.md`](../NORTH_STAR.md).

---

## 0. TL;DR

Taali already scores **how a candidate steers an agent on one task** (the AI-Fluency 4 Ds, via the
interrogation engine + per-dimension rubric grader). It does **not** score whether the candidate **works the
way an AI-native practitioner works** — sets up memory/context files, plans/specs before building, authors
reusable assets, keeps context clean, verifies with evals, and (for non-coding) gives brand/voice/source
context and reconciles facts. Today the harness *structurally cannot observe* this: the candidate gets a
single pinned-Haiku chat with four tools, **no** `CLAUDE.md`/`AGENTS.md`, **no** Skills, **no** plan mode, **no**
subagents (see `ASSESSMENT_AI_NATIVE_REVIEW.md` §1).

This design adds **AI-Native Practice Proficiency** as a graded construct, in two coupled moves:

1. **Expose the affordances** so the practice can be *exercised and observed* — a seeded, editable
   `AGENTS.md`; an explicit plan-/spec-first step; a "create a reusable asset" affordance; context-hygiene
   signals; controlled lookup; and a document-deliverable workspace for non-coding tasks.
2. **Capture + score** them through the **existing** rubric/lens/grader machinery, rolled **up into the 4 Ds**
   (not a new headline axis — the 5-axis scorecard stays canonical), and surfaced to recruiters as a
   "**how they set up and operated AI**" evidence layer.

Everything ships **additive, behind flags, shadow-validated** before any authoritative-score change — the
house rule from `NORTH_STAR.md` (Principle 4, determinism) and the impl plan's rollout pattern.

---

## 1. The construct: AI-Native Practice Proficiency

**Definition.** *Does the candidate operate their AI environment the way a strong AI-native practitioner does —
configuring context, planning before building, creating reusable leverage, keeping context clean, and verifying
— rather than treating the agent as a vague one-shot oracle?*

It is **not** a sixth scorecard axis. It is a **cross-cutting craft layer** whose observable behaviours each
roll up to one of the existing 4 Ds (this keeps `SCORING_SCORECARD.md`'s "one scorecard" invariant intact):

| Practice behaviour (observable) | Coding signal | Non-coding signal | Rolls up to |
|---|---|---|---|
| **Context/memory setup** | Creates/maintains a lean `AGENTS.md`/`CLAUDE.md`; gives the agent the right context | Gives source material, audience, brand/voice, constraints up front | **Description** |
| **Rich, specific direction** | Prompts with code/error context, examples, decomposition | Structured brief, examples, format/tone spec | **Description** |
| **Reusable assets** | Authors a `SKILL.md` / command / template / checklist | Builds a reusable template / style guide / checklist | **Description** |
| **Plan / spec-first** | Plans before building; writes/uses a spec for non-trivial work | Storyline/PR-FAQ/outline before slides or doc | **Delegation** |
| **Model/tool/approach choice & abandon** | Picks the right model/tool; knows when to abandon the AI path | Picks AI-draft vs. human-own split deliberately | **Delegation** |
| **Critical evaluation** | Catches wrong/incomplete output; rejects bad approach | Spots slop, off-brand, hallucinated facts | **Discernment** |
| **Verification & evals** | Runs tests/linter; re-reads diff; "explain every line" | Reconciles every number to a source of truth; fact-checks citations | **Diligence** |
| **Context hygiene** | Focused steps, clears stale context, just-in-time retrieval | Keeps the brief tight; doesn't dump irrelevant context | **Diligence** |

(Behaviour catalogue and "weak vs. strong" anchors are the proficiency ladder in
[`AI_NATIVE_BEST_PRACTICES.md`](./AI_NATIVE_BEST_PRACTICES.md) Part V.)

**Why it's defensible.** It is grounded in Anthropic's published AI-Fluency framework and the practitioner
consensus that *AI is an amplifier* and *verification is the bottleneck* — strong operators are separated from
weak ones precisely by these practices (DORA 2025; METR; Willison; Osmani — see best-practices doc). It is
**observable in the session** (Principle 1: "the session is the artefact") and **deterministically gradable**
where it counts (artifact presence + quality bands).

---

## 2. Design principles (inherited)

1. **One scorecard.** Practice proficiency rolls up into the 4 Ds; no new top-level axis (`SCORING_SCORECARD.md`).
2. **The session is the artefact.** Everything scored traces to something the candidate did, replayable later
   (`NORTH_STAR.md`).
3. **Reward genuine use tied to outcome, not cargo-culting.** A bloated, irrelevant `AGENTS.md` or a
   box-ticking "plan" scores *low* — mirroring the real anti-pattern ("the over-specified CLAUDE.md the agent
   ignores"). Practice only earns credit when it visibly improved the work.
4. **Determinism first.** Artifact-presence and structural checks are deterministic; the LLM grader judges
   *quality* at `temperature=0`. Two identical sessions get the same score.
5. **Additive & shadow-gated.** Land behind flags; shadow re-grade historical sessions; review the
   distribution/re-ranking with Sam; only then flip (impl-plan rollout pattern + PR-A shadow harness).
6. **Ecological validity vs. standardisation** is the known fork (`ASSESSMENT_AI_NATIVE_REVIEW.md` §6). We take
   **Option A now** (expose affordances *inside* the standardised harness) and **Option C (hybrid)** for senior
   roles later (a bring-your-own-tools segment).

---

## 3. End-to-end integration (start → finish)

### Stage 0 — Role & track configuration (recruiter side)
- A role/task declares a **track**: `coding` or `knowledge_work` (design / deck / PRD / plan / writing), plus a
  **seniority band**. Practice weight and which probes apply scale with seniority (juniors aren't penalised for
  not authoring a Skill; seniors are expected to).
- The existing `role_alignment` / `jd_to_signal_map` block already ties task artifacts to job requirements; we
  add practice probes to that map so each practice signal is JD-anchored (fairness/defensibility).

### Stage 1 — Candidate framing (fairness)
- The welcome screen states plainly what the environment supports ("you have an editable `AGENTS.md`, a
  plan step, and a scratchpad for reusable notes; use them as you would on the job"). Practice can only be
  fairly scored if every candidate knows it's available — otherwise we'd measure *guessing the harness*, not
  *practice*. This is a non-negotiable fairness gate.

### Stage 2 — Expose the affordances (the enabling changes)
Without these, practice is invisible. Each is additive and individually flag-gated.

**Coding track:**
- **A2.1 Seeded, editable `AGENTS.md`.** Ship a deliberately *thin/stale* `AGENTS.md` in the starter repo. A
  strong candidate prunes/improves it (or creates one); we capture the diff. (Repo already recreates a canonical
  repo snapshot per task — this is one more seeded file.)
- **A2.2 Explicit plan-/spec-first step.** The plan-before-build nudge already landed (impl-plan **PR-11**).
  Promote it to a *captured artifact*: a `PLAN.md` (or first-turn plan) the candidate writes before editing.
  Optional `spec_required` for senior tasks.
- **A2.3 "Reusable asset" affordance.** Offer a scratchpad/`SKILL.md` stub the candidate *may* use to capture a
  reusable check/checklist/command. Use is optional and only credited if coherent and used.
- **A2.4 Relaxed tool-call cap + step/verify guidance** (already landed, impl-plan **PR-3**) so a real
  read→edit→test loop is possible and observable.
- **A2.5 Controlled lookup** (deferred in impl-plan PR-11 for network-integrity reasons): a *curated, offline*
  doc source (no open web) so "real engineers look things up" is exercisable without breaking the sandbox
  network block. Score whether they look up vs. hallucinate.

**Knowledge-work track:**
- **A2.6 Document workspace.** Same E2B/Monaco repo surface, but seeded with **source material**
  (data tables, briefs, tickets) + a **brand/voice/style file** + a **template**, and the deliverable is a
  document (deck outline / PR-FAQ / PRD / design spec) rather than passing tests. (The existing
  `product_manager` and `scrum_master` tasks already prove non-coding tasks run on this surface — markdown
  briefs + a required output doc.)
- **A2.7 Storyline/spec-first probe.** The decision-point/interrogation opener asks the candidate to commit to a
  storyline/structure before generating slides/sections (the non-coding analogue of plan-first).

> **Senior hybrid (Option C, later):** for senior roles, an optional bring-your-own-tools segment (Claude
> Code/Cursor on a realistic repo) where full affordances exist natively and we capture the trace + a short
> "explain & defend" reflection (impl-plan PR-12). Higher realism, scored on the same construct.

### Stage 3 — Capture
Reuse the capture work already landed:
- **Artifacts:** `AGENTS.md` diff, `PLAN.md`/spec, `SKILL.md`/template, final repo/doc state, `git_evidence`
  (diff/commits) — all already captured at submit (`submission_runtime.py`).
- **Process trace:** candidate message → agent tool **calls + results** → agent text, interleaved (impl-plan
  **PR-1/PR-2**, `ASSESSMENT_GRADER_PROCESS_TRACE`). This is what lets the grader *see* verification behaviour.
- **Telemetry (evidence only):** plan-before-first-edit timing, context-reset events, lookup vs. hallucinate,
  number-reconciliation passes — surfaced to recruiters, not headline-scored (avoids gameable proxies driving
  the number).

### Stage 4 — Scoring (the core)
Practice is scored with the **existing** rubric machinery — no new engine:

- **4a. Deterministic artifact checks → an evidence sub-score.** A small, pure-Python `practice_outcome` grader
  (sibling to `interrogation_outcome`/`trap_outcome` in `rubric_scoring.py`) that scores *presence + structural
  quality* of declared artifacts: did they create/improve `AGENTS.md` (and is it lean, not bloated)? did a
  `PLAN.md` precede the first edit? did they author a coherent reusable asset? did they verify (test runs in
  the trace)? Deterministic, replayable, `temperature`-free.
- **4b. LLM quality bands via a new `practice` lens.** Add `_PRACTICE_LENS_PROMPT` to `rubric_scoring.py`
  (alongside the existing `discernment`/`diligence` lenses) that grades the *quality* of the practice from the
  trace + artifacts: was the context genuinely useful, the plan load-bearing, the asset reusable, the
  verification real — **not** box-ticking. `temperature=0`, one call per dimension, same as today.
- **4c. Fluency tagging (no new axis).** Each practice dimension carries a `fluency` tag so it rolls up to the
  right D via the *existing* `fluency_axis_for_dimension()` (it already honours an explicit `fluency` field).
  Memory/prompt/asset → `description`; plan/spec/model-choice → `delegation`; reject-slop → `discernment`;
  verify/hygiene → `diligence`.
- **4d. Weight in the per-task rubric.** Practice dimensions sit inside the task's `evaluation_rubric` and sum
  to 1.0 with the others (the loader enforces 4–6 dims, weights→1.0). Recommended practice weight: **15–25%**
  of the assessment rubric, **scaled by seniority** (junior ~10–15%, senior ~25%). It therefore flows through
  the *authoritative* `assessment_score` and into TAALI exactly like any other dimension — no separate pipeline.

How it reaches the headline number (unchanged plumbing, `TAALI_SCORING_RUBRIC.md`):
```
practice dimensions ─▶ evaluation_rubric (weights sum 1.0) ─▶ assessment_score
                                                              │
            Role fit (0.40 CV + 0.60 Requirements) ──────────┤
                                                              ▼
                  TAALI = 0.60·Assessment + 0.40·Role fit  (integrity caps applied after)
```

### Stage 5 — Reporting
- The 5-axis scorecard is unchanged; practice dimensions simply contribute to their D.
- Add a recruiter **"How they worked with AI"** evidence panel (drill-down under the 4 Ds), showing: the
  `AGENTS.md` diff, the plan, any reusable asset, the verification trace, and per-behaviour band + one-line
  rationale (the rubric already emits per-dimension rationale + evidence). Tooltip cites Anthropic's AI-Fluency
  framework (credibility asset, already the marketing line).

### Stage 6 — Calibration, fairness, anti-gaming
- **Anti-cargo-cult:** artifact *presence* alone caps at the "good" band; reaching "excellent" requires the LLM
  `practice` lens to confirm the practice was *load-bearing* (improved the work). A bloated/irrelevant
  `AGENTS.md` is explicitly a red flag in the rubric anchors.
- **Fairness:** seniority-scaled weights; framing in Stage 1; JD-anchoring via `jd_to_signal_map`; deterministic
  caps so two identical sessions match.
- **Anti-gaming hygiene:** keep tasks custom/ambiguous (already strong); refresh on a cadence; don't tune probes
  to one model's quirks (the practices are model-durable by design).
- **Self-perception probe (optional):** capture a candidate self-estimate of effectiveness vs. measured outcome
  — the gap is itself a validated weak-operator signal (METR perception gap).

---

## 4. Concrete spec & code changes

All additive; **zero migrations** (everything lives in existing JSON columns / task-spec JSON).

**Task-spec JSON (new optional fields):**
```jsonc
{
  "track": "coding" | "knowledge_work",
  "seniority": "junior" | "mid" | "senior",
  "context_files": { "AGENTS.md": "<thin/stale seed>", "BRAND_VOICE.md": "...", "DATA/orders.csv": "..." },
  "plan_required": true,                 // capture a PLAN.md / first-turn plan before edits
  "spec_required": false,                // senior coding/knowledge tasks
  "practice_probes": [                   // each becomes a rubric dimension
    { "id": "context_setup",  "fluency": "description", "expects": "improves AGENTS.md; lean, relevant" },
    { "id": "plan_first",     "fluency": "delegation",  "expects": "plan precedes first edit; load-bearing" },
    { "id": "reusable_asset", "fluency": "description", "expects": "coherent reusable check/template, used" },
    { "id": "verification",   "fluency": "diligence",   "expects": "tests/reconciliation in the trace" }
  ],
  "evaluation_rubric": {
    "context_and_direction": { "weight": 0.12, "lens": "practice", "fluency": "description",
      "criteria": { "excellent": "...lean, relevant context that demonstrably improved direction...",
                    "good": "...some context provided...", "poor": "...vague one-liners, no setup / bloated noise..." } },
    "plan_and_delegation":   { "weight": 0.08, "grader": "practice_outcome", "fluency": "delegation" }
    /* ...plus the task's existing decision/deliverable dims; weights re-balanced to sum 1.0 */
  }
}
```

**Backend (`backend/app/components/assessments/rubric_scoring.py`):**
- Add `_PRACTICE_LENS_PROMPT` + register in `_system_prompt_for_lens()` (mirrors the existing
  `discernment`/`diligence` lenses at lines ~331–370).
- Add a deterministic `grade_dimension_via_practice_outcome()` (sibling to the interrogation/trap graders, ~line
  565) that consumes the captured artifacts (`AGENTS.md` diff, `PLAN.md`, asset, test-run events from the trace).
- `fluency_axis_for_dimension()` already honours an explicit `fluency` tag — no change needed for rollup.

**Backend (`task_spec_loader.py`):** validate the new fields (`track`, `seniority`, `practice_probes`,
`fluency` enum already validated; extend the lens enum to include `practice`; keep the 4–6-dim / weights→1.0
validators).

**Backend (`submission_runtime.py`):** pass the practice artifacts into `ScoringArtifacts` (the process trace +
`git_evidence` are already wired by PR-2; add `AGENTS.md`/`PLAN.md`/asset capture from the final repo state).

**Harness (`claude_agent` service + candidate chat routes):** seed `context_files` into the repo snapshot;
surface the plan step + scratchpad in the candidate UI; (later) the controlled-lookup tool.

**Frontend:** the 5-axis scorecard is unchanged (`fluency4d.js`); add the "How they worked with AI" evidence
panel under the radar; glossary entry for "Practice proficiency" citing AI Fluency.

---

## 5. Worked examples

### 5a. Coding — extend `data_eng_data_quality_contract_framework`
Today it grades diagnosis (decision), `design_decisions_articulated` (interrogation), and two deliverable dims.
Add, re-balancing weights to 1.0:
- Seed a thin `AGENTS.md` (e.g. only "run pytest"); **`context_setup`** dim (`description`, ~0.08): did they
  enrich it with the venv/test command and the contract-spec pointer they kept re-reading?
- **`plan_first`** (`delegation`, ~0.07, `practice_outcome`): did a short plan precede editing the four stubs?
- **`verification`** (`diligence`, ~0.07, `practice_outcome`): did the trace show `pytest --tb=short` re-run
  after the gate change (the spec already lists "re-run the gate" as a strong-positive signal)?
- Interrogation + deliverable dims keep ~0.78 combined. Net: practice ≈ 22% of the rubric, all rolling into
  Delegation/Description/Diligence — no new axis.

### 5b. Knowledge-work — new task "Q3 Board Deck from raw numbers"
- **Track** `knowledge_work`, seniority `mid`. `context_files`: `FINANCE/q3_actuals.csv`, `BRAND_VOICE.md`
  ("direct, concrete, peer-to-peer"), `TEMPLATE/board_outline.md`.
- **Decision points (interrogation):** "Which single narrative does the deck argue — growth, efficiency, or
  runway? Pick; name what you de-emphasise." (Delegation.)
- **Rubric dims:**
  - `storyline_first` (delegation, `practice_outcome`): did an SCR/PR-FAQ outline precede slide drafting?
  - `context_and_voice` (description, `practice` lens): did they feed the brand/voice file and source data, and
    is commentary in-voice?
  - `number_reconciliation` (diligence, `practice` lens): did they reconcile every figure to `q3_actuals.csv`
    (the finance-team "single source of truth" check) — caught any that don't tie out?
  - `slop_discernment` (discernment): did they reject generic/hallucinated content?
  - `deliverable_quality` (deliverable): is the shipped outline/deck coherent and on-brand?
- Same engine, same 5-axis rollup, same TAALI math.

---

## 6. Rollout (phased, aligned to the existing tiers)

| Phase | Work | Gate |
|---|---|---|
| **P0 — enable observation** | Seed `context_files`/`AGENTS.md`; promote plan-step to captured `PLAN.md`; ensure PR-1/PR-2 trace flag on in shadow | additive; no score change |
| **P1 — machinery** | `_PRACTICE_LENS_PROMPT` + `practice_outcome` grader + loader validation + `practice_probes` | inert until a task adopts; unit tests green |
| **P2 — adopt on 2–3 flagship tasks** | Add practice dims to data_quality, genai_production_readiness, + one knowledge-work task; re-balance weights | **shadow re-score (PR-A) → review re-ranking with Sam → flip** |
| **P3 — reporting** | "How they worked with AI" evidence panel + glossary | verify on real authed candidate report page |
| **P4 — knowledge-work track + senior hybrid** | Document-deliverable tasks; optional BYO "explain & defend" segment | per-track shadow validation |

**Migrations:** none for P0–P3 (JSON columns + task-spec JSON). Optional indexed atomic `*_score` columns only
if we later want them queryable (one single-head migration, deferred).

---

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Cargo-culting** (empty `AGENTS.md`, box-tick plan) | Presence caps at "good"; "excellent" needs the LLM lens to confirm it was load-bearing; bloat is an explicit red flag |
| **Fairness** (candidate didn't know the affordance existed) | Stage-1 framing is a hard gate; seniority-scaled weights; JD-anchoring |
| **Score drift on adoption** | PR-A shadow gate; flag-gated; review distribution before flip |
| **Grader cost/latency** (bigger trace) | Bounded excerpts; per-dimension Sonnet grader already; monitor UsageEvents |
| **Tool-trivia bias** (rewarding Claude-specific knobs over durable judgment) | Score *durable* practices (context, plan, verify, reuse), model-agnostic; refresh tasks; don't tune to one model |
| **Standardisation vs. realism** | Option A now; Option C hybrid for senior only, where realism justifies the cost |

---

## 8. Success metrics (does this construct predict performance?)
Tie to `NORTH_STAR.md`'s killable claim. Track, post-launch:
- **Discrimination:** practice sub-scores spread candidates (not all-high/all-low) and correlate with the
  interrogation/deliverable dims without being redundant.
- **Incremental validity:** practice adds signal beyond Role fit + existing assessment dims (partial correlation
  with downstream outcome data, 12–18 mo).
- **Fairness/stability:** deterministic replay matches; no adverse band-flips by group in the shadow run.
- **Recruiter trust:** the "How they worked with AI" panel is used/cited in decisions.

---

*This is an additive continuation of the Option-A overhaul in `ASSESSMENT_AI_NATIVE_IMPL_PLAN.md`: that work made
the **process** visible and re-based scoring on the 4 Ds; this work scores the **practice craft** that the best
AI-native operators bring — for coding and knowledge work alike — without adding a sixth scorecard axis or a new
scoring engine.*
