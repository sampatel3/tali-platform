# Assessing AI-Native *Practice Proficiency* — Two-Part Integration Design

**Date:** 2026-06-27
**Status:** Design (recommended build — additive, flag-gated, shadow-validated before any score flip)
**Companions:** [`AI_NATIVE_BEST_PRACTICES.md`](./AI_NATIVE_BEST_PRACTICES.md) (what the practices are) ·
[`ASSESSMENT_AI_NATIVE_REVIEW.md`](./ASSESSMENT_AI_NATIVE_REVIEW.md) +
[`ASSESSMENT_AI_NATIVE_IMPL_PLAN.md`](./ASSESSMENT_AI_NATIVE_IMPL_PLAN.md) (process-visibility + 4 Ds rebase
this builds on) · [`SCORING_SCORECARD.md`](./SCORING_SCORECARD.md) · [`TAALI_SCORING_RUBRIC.md`](./TAALI_SCORING_RUBRIC.md) ·
[`NORTH_STAR.md`](../NORTH_STAR.md).

---

## 0. TL;DR — the recommended build

Taali already scores **how a candidate steers an agent on one task** (the AI-Fluency 4 Ds, via the
interrogation engine + per-dimension rubric grader). It does **not** score whether the candidate **works the
way an AI-native practitioner works** — sets up memory/context files, plans/specs first, authors reusable
assets, keeps context clean, verifies — for coding **or** for non-coding deliverables (design, decks, plans).
Today the harness *structurally cannot observe* this (single pinned-Haiku chat, 4 tools, no `CLAUDE.md`,
Skills, or plan mode — `ASSESSMENT_AI_NATIVE_REVIEW.md` §1).

**The build: a two-part assessment, dual-anchored to Anthropic.**

| Part | What it tests | Time | Anchored to | Produces |
|---|---|---|---|---|
| **Part 1 — Practice & Setup** (craft) | Does the candidate *set up and operate* their AI environment well? | ~10–15 min | **CCA-F domain taxonomy** (role-scaled) | **Practice** sub-score |
| **Part 2 — Applied Role Task** (judgment + deliverable) | Can they make and own the load-bearing calls and ship? | ~30 min | **AI Fluency 4 Ds** | **Applied** sub-score |
| *Part 0 (optional) — Unaided "explain & defend"* | Can they own/explain the result *without* the agent? | ~3–5 min | the "verification is the bottleneck" signal | integrity-style **modifier** |

```
Assessment = w₁·Practice + w₂·Applied        (default mid-level 30 / 70, seniority-scaled)
TAALI      = 0.60·Assessment + 0.40·Role fit  (Part-0 + integrity caps applied after)
```

Both parts grade through the **existing** rubric/lens/grader machinery and both roll up to the **same 5-axis
scorecard** (4 Ds + Deliverable) — so the two parts are *two evidence sources for one scorecard*, **not** a
sixth axis or a second vocabulary (`SCORING_SCORECARD.md`'s "one scorecard" invariant holds). Everything ships
**additive, behind flags, shadow-validated** before any authoritative-score change.

---

## 1. The two Anthropic anchors (and why both)

There are two distinct Anthropic frameworks; the design uses each for what it's actually for.

| Anchor | Certifies | Format | Used in Taali as |
|---|---|---|---|
| **AI Fluency: Framework & Foundations** (Anthropic Academy; the **4 Ds** — Delegation, Description, Discernment, Diligence) | *Working effectively with AI* | Free course + completion assessment | **The scoring construct** — already Taali's scorecard ([anthropic.com/learn/claude-for-you](https://www.anthropic.com/learn/claude-for-you)) |
| **Claude Certified Architect – Foundations (CCA-F)** (launched 12 Mar 2026; proctored, closed-book, 60 MCQ/120 min, 720/1000; 301-level, 6+ mo production Claude experience) | *Building production Claude systems* | Closed-book exam | **The craft taxonomy** for Part 1 (role-scaled) ([CCA-F domains overview](https://dev.to/aws-builders/the-claude-certified-architect-exam-5-domains-6-scenarios-and-everything-you-need-to-know-4le3)) |

**Why both, and why this split is honest:** CCA-F tests *knowledge* (closed-book MCQ, for people who *build*
agent systems); Taali tests *applied behaviour* (a real, observed work session for a *role*). They are
complementary — **knowing vs. doing** — and Taali occupies the space Anthropic does *not* fill: an applied,
per-person, role-grounded instrument. So we **anchor the construct on AI Fluency** and **borrow CCA-F's domain
map as Part 1's competency vocabulary**, without claiming to be or replace the certification.

### 1.1 CCA-F domains → Taali Part-1 practice probes → 4 Ds (role-scaled)

| CCA-F domain (exam weight†) | Engineering-role probe | Non-coding-role equivalent | Rolls up to |
|---|---|---|---|
| **Claude Code Workflows (20%)** | Maintains/prunes `AGENTS.md`/`CLAUDE.md`; plan-/spec-first; uses a reusable check/Skill | Sets up project/brief/template context; storyline-first | Description / Delegation |
| **Prompt Engineering & Structured Output (20%)** | Clear, contextful, decomposed prompts; examples; structured asks | Structured brief; audience/voice; format spec | Description |
| **Tool Design & MCP Integration (18%)** | Deliberate tool/lookup use; doesn't over-rely; *(senior)* tool/MCP choice | Uses source data; cites sources | Delegation / Diligence |
| **Context & Reliability (15%)** | Context hygiene; verification/tests; "explain every line" | Reconcile every number to a source of truth; fact-check | Diligence |
| **Agentic Architecture & Orchestration (27%)** | *(senior eng only)* decomposition, when to delegate/abandon, guardrails | Decompose work; AI-draft vs. human-own split | Delegation |

† **Caveat:** the 27/20/20/18/15 weights are corroborated across exam-prep sources but are **not** published in
detail by Anthropic — confirm against Anthropic's official exam guide before using them in marketing. Note we
**down-weight Agentic Architecture for most roles** (it's the builder/architect domain) and keep it only for
senior engineering — an honest role-scaling, not a 1:1 copy of an architect exam.

---

## 2. The construct: AI-Native Practice Proficiency

**Definition.** *Does the candidate operate their AI environment the way a strong AI-native practitioner does —
configuring context, planning before building, creating reusable leverage, keeping context clean, and verifying
— rather than treating the agent as a vague one-shot oracle?*

It is **not** a sixth scorecard axis. It is a **cross-cutting craft layer** whose observable behaviours each
roll up to one of the existing 4 Ds (behaviour catalogue + weak/strong anchors = the proficiency ladder in
[`AI_NATIVE_BEST_PRACTICES.md`](./AI_NATIVE_BEST_PRACTICES.md) Part V):

| Practice behaviour | Coding signal | Non-coding signal | Rolls up to |
|---|---|---|---|
| Context/memory setup | Lean `AGENTS.md`/`CLAUDE.md`; right context | Source material, audience, brand/voice up front | **Description** |
| Rich, specific direction | Code/error context, examples, decomposition | Structured brief, examples, format/tone spec | **Description** |
| Reusable assets | `SKILL.md` / command / template / checklist | Template / style guide / checklist | **Description** |
| Plan / spec-first | Plans/spec before building | Storyline/PR-FAQ/outline before slides/doc | **Delegation** |
| Model/tool choice & abandon | Right model/tool; knows when to abandon AI | AI-draft vs. human-own split deliberately | **Delegation** |
| Critical evaluation | Catches wrong/incomplete output | Spots slop / off-brand / hallucinated facts | **Discernment** |
| Verification & evals | Tests/linter; re-read diff; explain every line | Reconcile numbers to source; fact-check citations | **Diligence** |
| Context hygiene | Focused steps; clears stale context | Tight brief; no irrelevant dumping | **Diligence** |

---

## 3. Design principles (inherited)

1. **One scorecard.** Both parts roll up into the 4 Ds; no new top-level axis (`SCORING_SCORECARD.md`).
2. **The session is the artefact.** Everything scored traces to an observable, replayable action (`NORTH_STAR.md`).
3. **Reward genuine, load-bearing use — not cargo-culting.** A bloated/irrelevant `AGENTS.md` or a box-ticked
   "plan" scores *low*; presence alone caps at "good", "excellent" needs the practice to have visibly improved
   the work.
4. **Determinism first.** Artifact-presence/structural checks are deterministic; the LLM grader judges
   *quality* at `temperature=0`. Identical sessions get identical scores.
5. **Additive & shadow-gated.** Land behind flags → shadow re-grade → review re-ranking with Sam → flip
   (impl-plan rollout pattern + the PR-A shadow harness).
6. **Ecological validity vs. standardisation** is the known fork (`ASSESSMENT_AI_NATIVE_REVIEW.md` §6). We take
   **Option A now** (expose affordances *inside* the standardised harness) and **Option C (hybrid)** for senior
   roles later (a bring-your-own-tools segment in Part 1).

---

## 4. End-to-end flow (start → finish)

### Stage 0 — Role & track configuration (recruiter side)
- A role/task declares a **track** (`coding` | `knowledge_work`) and a **seniority band**; these set the
  Part-1/Part-2 weights, which Part-1 probes apply, and whether Part 0 runs.
- Part-1 probes are added to the existing `role_alignment` / `jd_to_signal_map` block so every practice signal
  is JD-anchored (fairness/defensibility).

### Stage 1 — Candidate framing (fairness gate)
- The welcome screen states plainly what the environment supports ("Part 1: ~10 min to set up your workspace
  and approach — editable `AGENTS.md`, a plan step, a scratchpad; Part 2: ~30 min on the task"). Practice can
  only be *fairly* scored if every candidate knows the affordances exist — otherwise we'd measure *guessing the
  harness*, not practice. **Non-negotiable.**

### Stage 2 — Part 1: Practice & Setup (~10–15 min, the new segment)
The candidate sets up and declares their approach before the main task. Each item is additive and flag-gated.

**Coding track:**
- **Seeded, editable `AGENTS.md`.** Ship a deliberately *thin/stale* `AGENTS.md`; capture the diff (strong
  candidates prune/enrich it). The repo already recreates a canonical snapshot per task — one more seeded file.
- **Plan-/spec-first artifact.** The plan-before-build nudge already landed (impl-plan **PR-11**); promote it
  to a captured `PLAN.md` written before editing (optional `spec_required` for senior tasks).
- **Reusable-asset affordance.** An optional `SKILL.md`/scratchpad stub for a reusable check/command; credited
  only if coherent and actually used.
- **Relaxed tool-call cap + step/verify guidance** (already landed, impl-plan **PR-3**) so a real
  read→edit→test loop is observable; **controlled offline lookup** (curated docs, no open web — respects the
  sandbox network block) so "look it up vs. hallucinate" is testable.
- *(Senior, Option C):* optional bring-your-own-tools sub-segment (Claude Code/Cursor) where the affordances
  exist natively; capture the trace.

**Knowledge-work track:**
- **Document workspace** seeded with **source material** (data tables, briefs, tickets) + a
  **brand/voice/style file** + a **template**; deliverable is a document (deck outline / PR-FAQ / PRD / design
  spec). The existing `product_manager` and `scrum_master` tasks already prove non-coding tasks run on this
  surface.
- **Storyline/structure-first probe** — commit to an outline/structure before generating content (the
  non-coding analogue of plan-first).

### Stage 3 — Part 2: Applied Role Task (~30 min, existing)
The current interrogation + deliverable task, unchanged in spirit — the load-bearing-decision-ownership engine
that is Taali's moat (`ASSESSMENT_AI_NATIVE_REVIEW.md` §0). Scored on the 4 Ds + Deliverable as today.

### Stage 4 — Part 0 (optional): Unaided "explain & defend" (~3–5 min)
A short segment with **the agent disabled**: the candidate explains/defends one decision or walks the diff in
their own words. Directly tests comprehension/ownership (the "verification is the bottleneck" signal) and
hardens against over-reliance and identity fraud — mirroring that Anthropic disallows AI in some of its own
hiring stages. **Default off**, recruiter-enabled per role; scored as an **integrity-style modifier** on the
Assessment (a strong defence confirms ownership; a candidate who can't explain their own submission is capped),
not as a separate headline axis — so it can't be gamed into inflating the score.

### Stage 5 — Capture (reuse what landed)
- **Artifacts:** `AGENTS.md` diff, `PLAN.md`/spec, `SKILL.md`/template, final repo/doc, `git_evidence` — all
  captured at submit (`submission_runtime.py`).
- **Process trace:** candidate message → agent tool **calls + results** → agent text, interleaved (impl-plan
  **PR-1/PR-2**, `ASSESSMENT_GRADER_PROCESS_TRACE`) — what lets the grader *see* verification behaviour.
- **Telemetry (evidence only):** plan-before-first-edit timing, context-reset events, lookup-vs-hallucinate,
  number-reconciliation passes — surfaced to recruiters, not headline-scored (no gameable proxies drive the
  number).

### Stage 6 — Scoring (the core)
Both parts grade with the **existing** machinery — no new engine.

- **6a. Deterministic artifact checks → evidence sub-score.** A pure-Python `practice_outcome` grader (sibling
  to `interrogation_outcome`/`trap_outcome` in `rubric_scoring.py`) scoring *presence + structural quality*: did
  they create/improve `AGENTS.md` (lean, not bloated)? did `PLAN.md` precede the first edit? a coherent reusable
  asset? verification in the trace? Deterministic, `temperature`-free.
- **6b. LLM quality bands via a new `practice` lens.** Add `_PRACTICE_LENS_PROMPT` to `rubric_scoring.py`
  (beside the existing `discernment`/`diligence` lenses, ~lines 331–370) grading whether the practice was
  *load-bearing* — genuinely-useful context, a real plan, a reusable asset, real verification — **not**
  box-ticking. `temperature=0`, one call per dimension.
- **6c. Fluency tagging (no new axis).** Each Part-1 dimension carries a `fluency` tag → rolls to the right D
  via the *existing* `fluency_axis_for_dimension()` (already honours an explicit `fluency` field).
- **6d. Two part-rubrics → one Assessment.** Part 1 has its own small `evaluation_rubric` (weights sum 1.0);
  Part 2 keeps today's. The headline Assessment is the **weighted blend** of the two part scores; the 5-axis
  rollup (`summarize_fluency_4d`) aggregates *both* parts' dimensions, so the scorecard stays unified.

**Weights (seniority-scaled):**

| Band | Part 1 Practice (`w₁`) | Part 2 Applied (`w₂`) | Part 0 unaided |
|---|---|---|---|
| Junior | 20% | 80% | off |
| Mid | 30% | 70% | optional |
| Senior | 35% | 65% | on (modifier) |

Applied stays dominant — the role task is the strongest predictive signal; Practice is the differentiator that
separates strong operators at the same task-output level.

```
Part 1 Practice ─┐
                 ├─▶ Assessment = w₁·Practice + w₂·Applied
Part 2 Applied  ─┘                    │
        Part 0 defence (modifier/cap) ─┤
  Role fit (0.40 CV + 0.60 Requirements)┤
                                        ▼
        TAALI = 0.60·Assessment + 0.40·Role fit   (integrity caps applied after)
```

### Stage 7 — Reporting
- 5-axis scorecard unchanged; both parts contribute to their Ds.
- Add a recruiter **"How they worked with AI"** evidence panel (drill-down under the 4 Ds): the `AGENTS.md`
  diff, the plan, any reusable asset, the verification trace, the unaided-defence verdict, and per-behaviour
  band + one-line rationale (the rubric already emits per-dimension rationale + evidence). Tooltip cites
  Anthropic's **AI Fluency** framework, with Part 1 labelled against the CCA-F competency areas.

### Stage 8 — Calibration, fairness, anti-gaming
- **Anti-cargo-cult:** presence caps at "good"; "excellent" needs the LLM `practice` lens to confirm
  load-bearing use; bloat is an explicit red flag in the rubric anchors.
- **Fairness:** seniority-scaled weights; Stage-1 framing; JD-anchoring; deterministic caps so identical
  sessions match. Role-scale the CCA-F taxonomy — never grade a designer on MCP.
- **Don't overclaim the cert:** Part 1 is "aligned to the competency areas Anthropic certifies," the whole
  assessment is "built on Anthropic's AI Fluency framework" — never "the Anthropic certification."
- **Anti-gaming hygiene:** keep tasks custom/ambiguous; refresh on a cadence; the practices are model-durable
  by design, so don't tune probes to one model's quirks.
- **Self-perception probe (optional):** candidate self-estimate vs. measured outcome — the gap is itself a
  validated weak-operator signal (METR perception gap).

---

## 5. Concrete spec & code changes

All additive; **zero migrations** (everything lives in existing JSON columns / task-spec JSON).

**Task-spec JSON (new optional fields):**
```jsonc
{
  "track": "coding" | "knowledge_work",
  "seniority": "junior" | "mid" | "senior",
  "part_weights": { "practice": 0.30, "applied": 0.70 },   // seniority default; recruiter-overridable
  "unaided_defense": { "enabled": false },                  // Part 0
  "context_files": { "AGENTS.md": "<thin/stale seed>", "BRAND_VOICE.md": "...", "DATA/orders.csv": "..." },
  "plan_required": true,
  "spec_required": false,
  "practice_rubric": {                                      // Part 1's own rubric (weights sum 1.0)
    "context_and_direction": { "weight": 0.40, "lens": "practice",        "fluency": "description",
      "criteria": { "excellent": "...lean, relevant context that demonstrably improved direction...",
                    "good": "...some context provided...", "poor": "...vague one-liners / bloated noise..." } },
    "plan_and_delegation":   { "weight": 0.30, "grader": "practice_outcome", "fluency": "delegation" },
    "verification_habit":    { "weight": 0.30, "grader": "practice_outcome", "fluency": "diligence" }
  },
  "evaluation_rubric": { /* Part 2 — unchanged: decision / interrogation / deliverable dims */ }
}
```

**Backend (`backend/app/components/assessments/rubric_scoring.py`):**
- `_PRACTICE_LENS_PROMPT` + register in `_system_prompt_for_lens()` (mirrors `discernment`/`diligence`).
- `grade_dimension_via_practice_outcome()` (sibling to the interrogation/trap graders, ~line 565) consuming the
  captured artifacts (`AGENTS.md` diff, `PLAN.md`, asset, test-run events from the trace).
- `summarize_fluency_4d()` already aggregates any list of graded dims — feed it Part 1 + Part 2 dims together.
- `fluency_axis_for_dimension()` already honours the explicit `fluency` tag — no rollup change.

**Backend (`task_spec_loader.py`):** validate `track`, `seniority`, `part_weights`, `practice_rubric`,
`unaided_defense`; extend the lens enum to include `practice`; keep the 4–6-dim / weights→1.0 validators
(applied per-rubric).

**Backend (`submission_runtime.py`):** compute `practice_score` and `applied_score`, blend by `part_weights`
into `assessment_score`; apply the Part-0 defence verdict as a modifier (alongside the existing integrity caps);
pass Part-1 artifacts into `ScoringArtifacts`.

**Harness (`claude_agent` service + candidate chat routes):** seed `context_files`; surface the Part-1 setup UI
(plan step, scratchpad, `AGENTS.md` editor) and the optional Part-0 unaided step; (later) controlled lookup.

**Frontend:** 5-axis scorecard unchanged (`fluency4d.js`); add the "How they worked with AI" evidence panel and
a "Practice proficiency" glossary entry citing AI Fluency + the CCA-F competency areas.

---

## 6. Worked examples

### 6a. Coding — extend `data_eng_data_quality_contract_framework`
- **Part 1 (Practice, `w₁`=0.30):** seed a one-line `AGENTS.md`; `practice_rubric` =
  `context_and_direction` (description) — did they enrich it with the venv/test command + the contract-spec
  pointer they kept re-reading? · `plan_and_delegation` (delegation) — short plan before editing the four
  stubs? · `verification_habit` (diligence) — `pytest --tb=short` re-run after the gate change (already a
  strong-positive signal in the spec)?
- **Part 2 (Applied, `w₂`=0.70):** today's diagnosis + `design_decisions_articulated` (interrogation) + two
  deliverable dims, unchanged.
- Both feed the same 5-axis rollup; Assessment = 0.30·Practice + 0.70·Applied.

### 6b. Knowledge-work — new task "Q3 Board Deck from raw numbers"
- **Track** `knowledge_work`, seniority `mid`. `context_files`: `FINANCE/q3_actuals.csv`, `BRAND_VOICE.md`,
  `TEMPLATE/board_outline.md`.
- **Part 1 (Practice):** `context_and_voice` (description) — fed the brand/voice file + source data, commentary
  in-voice? · `storyline_first` (delegation, `practice_outcome`) — SCR/PR-FAQ outline before slides? ·
  `number_reconciliation` (diligence, `practice` lens) — every figure tied to `q3_actuals.csv`?
- **Part 2 (Applied):** interrogation decision point ("which single narrative — growth, efficiency, runway? pick
  and name what you de-emphasise") + `slop_discernment` (discernment) + `deliverable_quality` (deliverable).
- **Part 0 (optional):** 3-min unaided defence of the chosen narrative.
- Same engine, same 5-axis rollup, same TAALI math.

---

## 7. Rollout (phased, flag-gated, aligned to existing tiers)

| Phase | Work | Gate |
|---|---|---|
| **P0 — enable observation** | Seed `context_files`/`AGENTS.md`; promote plan-step to captured `PLAN.md`; PR-1/PR-2 trace flag on in shadow | additive; no score change |
| **P1 — machinery** | `_PRACTICE_LENS_PROMPT` + `practice_outcome` grader + `practice_rubric` loader validation + `part_weights` blend | inert until a task adopts; unit tests green |
| **P2 — adopt Part 1 on 2–3 flagship tasks** | Add `practice_rubric` to data_quality, genai_production_readiness, + one knowledge-work task; set weights | **shadow re-score (PR-A) → review re-ranking with Sam → flip** |
| **P3 — reporting** | "How they worked with AI" evidence panel + glossary + CCA-F/AI-Fluency labels | verify on a real authed candidate report page |
| **P4 — knowledge-work track + Part 0 + senior hybrid** | Document-deliverable tasks; unaided defence; optional BYO sub-segment | per-track shadow validation |

**Migrations:** none for P0–P3 (JSON columns + task-spec JSON). Optional indexed atomic columns later (one
single-head migration, deferred).

---

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| **Cargo-culting** (empty `AGENTS.md`, box-tick plan) | Presence caps at "good"; "excellent" needs the LLM lens to confirm load-bearing use; bloat is a red flag |
| **Two parts feel disjoint / lengthen the assessment** | Part 1 capped ~10–15 min; framed as one arc (set up → do the work); both feed one scorecard |
| **Overclaiming the cert** | "Aligned to" the CCA-F competency areas, "built on" AI Fluency — never "is the Anthropic certification"; weights flagged unconfirmed |
| **Fairness** (candidate didn't know affordances existed) | Stage-1 framing is a hard gate; seniority-scaled weights; JD-anchoring; role-scaled CCA-F taxonomy |
| **Score drift on adoption** | PR-A shadow gate; flag-gated; review distribution before flip |
| **Grader cost/latency** (extra Part-1 calls) | Bounded excerpts; per-dimension grader already; monitor UsageEvents |
| **Tool-trivia bias** | Score *durable*, model-agnostic practices (context, plan, verify, reuse); refresh tasks |

---

## 9. Success metrics (does this predict performance?)
Tie to `NORTH_STAR.md`'s killable claim. Post-launch, track:
- **Discrimination:** Practice sub-scores spread candidates and correlate with the interrogation/deliverable
  dims without being redundant.
- **Incremental validity:** Practice adds signal beyond Role fit + Applied (partial correlation with downstream
  outcomes, 12–18 mo).
- **Fairness/stability:** deterministic replay matches; no adverse band-flips by group in the shadow run.
- **Recruiter trust:** the "How they worked with AI" panel is used/cited in decisions.

---

*An additive continuation of the Option-A overhaul in `ASSESSMENT_AI_NATIVE_IMPL_PLAN.md`: that work made the
**process** visible and re-based scoring on the 4 Ds; this work adds a **two-part assessment** — a CCA-F-aligned
**Practice & Setup** segment plus the AI-Fluency-anchored **Applied** task — scoring the practice craft that the
best AI-native operators bring, for coding and knowledge work alike, without a sixth scorecard axis or a new
scoring engine.*
