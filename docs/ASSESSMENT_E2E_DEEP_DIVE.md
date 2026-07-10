# Candidate Assessment — End-to-End Deep Dive

**Date:** 2026-07-10 · **Author:** research pass (internal code/data audit + external primary-source research)
**Companions:** `docs/ASSESSMENT_AI_NATIVE_REVIEW.md` (2026-06-26), `docs/AI_NATIVE_PRACTICES_ASSESSMENT_INTEGRATION.md` (two-part design), `docs/SCORING_SCORECARD.md` (the 5 Ds)

---

## Verdict (TL;DR)

**The machinery works. The instrument is unvalidated. The bottleneck is volume, not features.**

1. **Every layer of the pipeline is built and most of it is proven** — JD→task generation, repo provisioning, invite delivery tracking, the live Claude-pairing runtime, per-turn telemetry, rubric scoring on the 5 Ds, the interrogation engine, integrity/void, timeout finalization. The capture→grade path was verified end-to-end on real E2B sandboxes.
2. **Almost none of it has met a real candidate.** All-time real-candidate funnel: **65 assessments created → 37 emailed → 34 started → 14 completed** — and 7 of the 12 clean completions had under 2 minutes of active time. Last invite went out **2026-06-25**. Delivery tracking (live since 06-14) has never captured a single real invite.
3. **The two-stage pivot is machinery without evidence.** Zero production tasks are two-stage. The only two-stage runs ever are three of Sam's own tests on two hand-authored test tasks (2026-06-28). Skepticism is warranted — not because the idea is wrong, but because it is unvalidated and, as designed (announced stage, 30% weight), gameable. Recommendation below: score practice **observed, not announced**, and A/B the announced version.
4. **The highest-value scoring gap is not two-stage — it's verification.** Zero active tasks use the discernment or diligence lenses; zero tasks have traps. The strongest external evidence (Anthropic, BCG/Harvard, METR) says *catching the AI's errors and verifying before shipping* is the #1 discriminator of skilled AI use. We built the lenses (PR #716) and never adopted them into a single rubric.
5. **Task auto-generation already exists and already ran** — 10 role-specific draft tasks were generated from JDs (June–July) and **all 10 are stuck at `needs_review` with zero activated and zero sent**. The missing piece is not generation; it's an automated battle-test + one-page report card that makes human approval a 2-minute decision.

Priority order: **(P0) restart volume + instrument the first 10 minutes → (P1) score the verification half → (P1) unblock the task-generation review gate → (P2) validate two-stage via A/B → (P2) engagement/nudge sequence.** Details in §6.

---

## 1. Where the system stands — evidence

### The funnel (prod, all-time, excluding Sam's test accounts)

| Stage | Count | Notes |
|---|---|---|
| Created | 65 | first 2026-03-03 |
| Emailed | 37 | last invite **2026-06-25** — pipeline idle since |
| Delivery-tracked | **0** | tracking live since 06-14 (#642/#646); no real invite sent since |
| Started | 34 | |
| Started → expired (work discarded pre-#698) | 20 | timeout finalization now fixes this class |
| Completed | 14 | 12 clean + 2 `COMPLETED_DUE_TO_TIMEOUT` |
| Completed with >2 min active time | 7 | avg active time of clean completions: 25.5 min |

Read: **we have ~7 meaningful data points ever.** No scoring design debate can be settled at this volume. Everything in §2–§5 is sequenced around fixing that first.

### Feature adoption (prod DB, 2026-07-10)

| Capability | Built | Adopted by a live task | Seen by a real candidate |
|---|---|---|---|
| 5-Ds rubric scoring (Sonnet, temp 0) | ✅ #716/#746 | ✅ (rubric tasks) | ✅ (a handful) |
| Interrogation engine (decision points) | ✅ | ✅ 10 tasks, weight 0.35–0.40 | ✅ |
| Process trace → grader (tool results, git) | ✅ permanent since #725 | ✅ | ✅ |
| **Discernment / diligence lenses** | ✅ #716 | **❌ 0 dims on 0 tasks** | ❌ |
| **Traps (planted wrong-but-plausible paths)** | ✅ #716 | **❌ 0 tasks** | ❌ |
| **Two-stage (practice + applied blend)** | ✅ #776/#797 | **❌ 0 real tasks** (2 test tasks, org 2) | ❌ (3 runs, all Sam) |
| JD→task auto-generation | ✅ (`task_spec_generator.py`) | **10 drafts generated, 0 activated** | ❌ |
| A/B experiment infra | ✅ #514 (Auto split) | ✅ data-eng roles | barely (tiny N) |
| Delivery tracking (Resend webhook) | ✅ #642/#646 | ✅ | **0 events ever** |

Active-task lens census: `deliverable` 23 dims / `decision` 16 / `interrogation` 15 / `practice` 2 (both test tasks) / `discernment` + `diligence` **0**.

### Architecture in one paragraph

A task is a declarative JSON spec (`backend/tasks/*.json` + org-owned rows in `tasks`): scenario, `decision_points`, lens rubric (weights sum to 1.0, enforced by `task_spec_loader.validate_task_spec`), starter `repo_structure`, `test_runner`, `role_alignment` with a `jd_to_signal_map` covering every rubric dimension. Send provisions a GitHub branch (`taali-ai/<task_key>`, branch `assessment/<id>`) and emails via Resend (`invite_flow.dispatch_assessment_invite`). Start boots an E2B sandbox; the candidate pairs with Claude (Haiku, server-side Agent SDK, sandbox tools Read/Write/Edit/Bash) in a web workspace with an explicit start gate and a 30-min pause-aware timer. Every turn is captured to `assessments.ai_prompts` (message, response, tokens, latency, tool calls **and results**, paste/focus flags, interrogation state) plus an append-only `timeline`. Submit runs the task's test runner, snapshots the repo and git evidence, then `RubricScorer` (Sonnet 4.5, temperature 0, one metered call per dimension, verbatim evidence citations required) grades each dimension through its lens; deterministic graders (`interrogation_outcome`, `practice_outcome`) score without model calls. Dimensions roll up to the 5 Ds (`fluency_4d` in `score_breakdown`) and, if a task has practice dims, to the two-stage part blend. The score feeds `taali_score` and the Decision Hub.

---

## 2. Measuring AI-tool fluency — engineering and knowledge work

### What we do today, and how it compares to the best available evidence

Our scorecard is anchored on **Anthropic's AI Fluency framework (Delegation, Description, Discernment, Diligence)** plus Deliverable — user-facing name: **the 5 Ds**. External check: this remains the *only* published, citable AI-collaboration competency model ([aifluencyframework.org](https://aifluencyframework.org/), [Anthropic course](https://anthropic.skilljar.com/ai-fluency-framework-foundations)). No vendor (CodeSignal, HackerRank) publishes a scoring rubric; no psychometrically validated AI-fluency instrument exists for hiring, coding or otherwise. **Our construct choice is sound and defensible, and the gap in the market is real.**

What the strongest empirical work says distinguishes skilled from unskilled AI-assisted work:

- **Discernment is the differentiator.** Dell'Acqua et al. (758 BCG consultants, [SSRN 4573321](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4573321)): skilled users selectively delegate and verify; unskilled users accept plausible-but-wrong output ("asleep at the wheel"). This holds for knowledge work, not just code.
- **Self-report is worthless; behavior is everything.** METR's RCT ([arXiv:2507.09089](https://arxiv.org/abs/2507.09089)): experienced devs *believed* AI sped them up ~20% while actually being 19% slower. Any signal that isn't observed behavior is noise.
- **Anthropic's own take-home redesign** ([AI-resistant technical evaluations](https://www.anthropic.com/engineering/AI-resistant-technical-evaluations)): allow AI and measure the human's value *on top of* the model; measure secondary skills (tooling judgment, debugging strategy, comprehension) as scaling-robust; expect every model release to eat yesterday's task difficulty.

**Where we're strong:** the interrogation engine (decision ownership, scored deterministically) is exactly the "judgment over output" signal the research points at, and it's live on 10 tasks. The deliverable/decision lens split correctly refuses to punish delegation on the artifact.

**Where we're structurally weak:** the verification half. Zero tasks carry a discernment or diligence dimension, so "did they test, challenge, or blindly accept Claude's output" — the #1 discriminator — never contributes to a score. The lenses, the process trace they need, and the trap mechanic all shipped in #716; adoption is a task-JSON edit plus a shadow re-score, not an engineering project.

### The two-stage pivot — honest assessment

The design (PR #776/#797): Part 1 "Practice & Setup" (~10–15 min; CLAUDE.md/AGENTS.md authoring, plan-first, reusable assets, verification habits) + Part 2 "Applied Role Task" (~30 min), blended 30/70 into the authoritative score. Machinery quality is good: back-compatible, `practice_outcome` grader caps ritual compliance at "good", bloated context files score *poor* (the right anti-pattern), only the LLM `practice` lens can award "excellent" for demonstrably load-bearing craft.

The case for skepticism, with the evidence:

1. **Zero validation.** No real task, no real candidate, three self-runs. Two of those timed out with `applied = 0.0`, blending to ~10/100 — correct math, but it means we've never even seen a healthy two-stage score.
2. **Announced stages are gameable.** A candidate told "Part 1 scores your setup" writes a CLAUDE.md because it's scored, not because it helps — a knowledge check ("have you read the best-practices posts") wearing a behavior costume. The grader's "load-bearing" test mitigates but is judged from a 10–15 minute window where almost nothing has had time to *bear load*.
3. **Construct mismatch with the timebox.** Anthropic's best-practices guidance positions context files as infrastructure for *ongoing* work. In a 30-minute one-shot, the practices that genuinely move the outcome are plan-first and verification — both of which are (or should be) scored inside the applied task via decision/diligence lenses. Part 1 spends up to 30% of the score and a third of the clock on the practice least able to show its value in-window.
4. **Opportunity cost.** The same grading budget spent adopting discernment/diligence dims buys the signal with the strongest external evidence behind it.

**Recommendation — observe, don't announce (then A/B the announced version):**

- Keep all the two-stage machinery. It costs nothing while dormant.
- Fold practice into the applied task as **observed signals**: seed the repo with the affordances (empty `CLAUDE.md`, a `PLAN.md` template, a note in the brief that says "set up the workspace however you'd genuinely work"), and let the existing `practice_outcome` probes + `practice` lens grade what candidates *actually did*, as 1–2 low-weight dims (10–15% total) inside the normal rubric. No stepper, no announced part, no separate clock. This keeps the signal and removes the theater incentive.
- Author ONE flagship two-stage task and run it as an **A/B arm** against the observed-practice variant using the experiment infra we already have (`experiment_assignment.py`, Auto split since #514). Decide on completion-rate, score-discrimination, and time-use data, not intuition. Win condition per the existing experiment policy: don't declare below 20 completions/arm.
- Seniority scaling (the 30/70 → senior-heavier-applied idea) only matters once the above resolves; park it.

### Knowledge work

The harness already supports doc-kind deliverables (`deliverable.kind: "doc"`, primary artifact a memo the candidate writes with Claude; PM/scrum/security-governance tasks exist), and the 5 Ds transfer cleanly — the BCG and P&G field experiments ([Cybernetic Teammate, SSRN 5188231](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5188231)) show AI-collaboration skill is a real performance factor in general knowledge work. External research found **no validated instrument for non-coding AI fluency — nobody owns this yet.**

To make doc tasks first-class rather than ports of code tasks:

- **Verification looks different for documents.** For code, diligence = run the tests. For docs, diligence = checking Claude's claims against the input briefs, asking for sources, catching a planted factual error. The trap mechanic is *more* natural here: seed one plausible-but-wrong fact in the input pack; a discernment dim scores whether it survives into the memo. (Traps shipped in #716; zero adoption.)
- **The test runner already handles docs** (section-coverage checks). Add a claim-level check: a grading-time comparison of memo assertions vs brief facts — this is a `deliverable`-lens criteria change, not new machinery.
- The generator already handles doc-kind (`_deliverable_kind_hint`, `task_spec_generator.py`) — so knowledge-work coverage scales with the same task pipeline as engineering (§4).

---

## 3. Telemetry — what to capture and what to score from it

### What we capture today (strong)

Per turn in `ai_prompts`: message, response, per-turn tokens (incl. cache), model, latency, `tool_calls_made` **with results** (PR-1; truncated 600 chars), `code_context`, paste/focus flags, `time_since_last_prompt_ms` (client-supplied), timestamp, interrogation state. Plus: append-only `timeline` events (`ai_prompt`, `first_prompt`, `code_execute` with test counts, `repo_file_save`, `integrity_flag`), `test_results`, `final_repo_state` (≤40 files), `git_evidence` (diff + log), fraud flags, token totals, tab switches. The grader sees transcript + tool actions/results + git evidence + tests (`ScoringArtifacts`, `rubric_scoring.py`).

This is already ahead of the commercial baseline — CodeSignal's AI-assisted assessments give reviewers a transcript and a replay to *read*; we grade ours automatically.

### What best practice says to add (and what not to bother with)

Claude Code's own OTel surface ([monitoring docs](https://code.claude.com/docs/en/monitoring-usage.md)) defines the industry-shape of process telemetry: `tool_decision accept|reject`, `user_prompt`, `tool_result` success/duration, active time, lines added/removed, with `prompt.id` + sequence for loop reconstruction. Our SDK-chat harness auto-executes tools, so accept/reject doesn't exist structurally — but the *equivalent* signal is derivable from what we already store: did the candidate's next turn correct, revert, or challenge what Claude just did?

**Recommendation — derive, persist, and feed a small deterministic feature set at submit time** (pure post-processing of `ai_prompts` + `timeline`; no new capture, no migration — it's JSON):

| Feature | From | Why (evidence) |
|---|---|---|
| `verification_events` — test runs / re-reads after edits, and whether any preceded submit | `timeline.code_execute`, tool calls | BCG: verification is THE discriminator; deterministic red flag: zero verification before submit |
| `refinement_ratio` — follow-up prompts that build on prior output vs from-scratch re-rolls | message similarity across turns | Anthropic's description→discernment loop; refine-not-reroll is the taught skilled behavior |
| `challenge_events` — turns where the candidate contradicts/corrects Claude or asks it to justify | transcript classifier (Haiku, or fold into interrogation pass) | discernment made countable; also guards the LLM-judge self-enhancement trap |
| `deference_score` — % of Claude actions never questioned, never verified | tool results × subsequent turns | both extremes are signal (rubber-stamp / ignore) — Copilot-era acceptance-rate research ([CACM](https://cacm.acm.org/research/measuring-github-copilots-impact-on-productivity/)) |
| `prompt_cadence` — server-validated inter-turn gaps, time-to-first-prompt, idle stretches | server timestamps (stop trusting `time_since_last_prompt_ms`, it's client clock) | "single mega-prompt then idle" is a deterministic red flag |

Feed these two places: (a) recruiter evidence panels (they're display-ready), and (b) **as structured context into the discernment/diligence lens prompts** — the LLM judge grades better when the deterministic loop skeleton is handed to it rather than inferred from a 20-turn excerpt.

**LLM-as-judge hygiene** (we already do most of this — keep it): per-criterion grading, temperature 0, required verbatim evidence citations. Add two controls from the judge-bias literature ([MT-Bench, arXiv:2306.05685](https://arxiv.org/abs/2306.05685); [arXiv:2510.12462](https://arxiv.org/html/2510.12462v3)): an explicit anti-verbosity instruction in each lens prompt (long prompts/sessions ≠ skill), and never pairwise candidate-vs-candidate comparison (position bias). The self-enhancement trap — a Claude judge over-scoring deference to Claude — is exactly why `challenge_events` and the discernment lens matter: they make "caught Claude's mistake" an explicit reward.

**Don't bother with:** keystroke/cursor telemetry, camera proctoring, more browser-focus signals. They're weak, spoofable, and hostile to candidate experience; our integrity engine (injection/probe detection, warn→void) plus scoring-side controls (interrogation, fraud caps) is the right posture and is already live.

One real capture gap worth eventually closing: **per-edit file history** (we snapshot at save/submit, so intra-session evolution of a file is invisible). Moderate effort; only justified once volume makes replay analysis useful.

---

## 4. A design framework so every job gets a task automatically

### What exists (more than expected)

- **The design contract is codified and enforced.** Every task must satisfy `validate_task_spec`: lens rubric summing to 1.0 with exactly one interrogation dim, 2–3 decision points with anti-patterns, deliverable schema, meaningfully-failing baseline tests, `jd_to_signal_map` covering every dimension (#537). This *is* the "all tasks follow the same design" guarantee.
- **JD→task generation is built and works** (`task_spec_generator.py`): Sonnet authors a complete spec from the role + JD under the 7-lever framework, with a bounded generate→validate→repair loop; `task_provisioning_service.py` persists it as an org-owned **draft** (`is_active=False, needs_review=True`), provisions the template repo, links the role. Triggers: role creation and Workable job sync, gated by `AUTO_GENERATE_ASSESSMENT_TASKS`; recruiter revision via agent chat (`agent_chat/draft_tasks.py`) round-trips feedback through `revise_task_spec`.
- **It has already run 10 times in prod** (secops, principal seceng, devops, full-stack, AI tester, AI devops, UI eng, senior AI eng, fullstack AI — June 2 → July 2). **All 10 drafts are still `needs_review`. Zero activated. Zero sent.**

So the framework question is not "how do we generate tasks" — it's **"why does nothing get through the review gate, and what makes generated tasks trustworthy enough to send?"**

### External evidence on generated assessments

- **Per-job tasks are the contamination defense, not just a relevance nicety.** interviewing.io's controlled cheating study: ChatGPT-assisted pass rates were 73% on verbatim public questions, 67% on lightly modified, **25% on genuinely custom** — and interviewers detected none of the cheaters ([interviewing.io](https://interviewing.io/blog/how-hard-is-it-to-cheat-with-chatgpt-in-technical-interviews)). Anthropic reached the same conclusion from the model side. Custom structure per job is the moat.
- **Generated ≠ calibrated.** The only mature first-party practice is Duolingo English Test: generate → automated screens → human review → **empirical difficulty calibration (AutoIRT, [arXiv:2409.08823](https://arxiv.org/abs/2409.08823)) before items count**. Nobody has published psychometric validation of LLM-generated *work-sample* tasks — we'd be ahead of published practice, which cuts both ways: opportunity, and no safety net.
- **Fairness/defensibility:** if two candidates for the same role can get different generated tasks, difficulty equivalence becomes a selection-defensibility issue. The psychometric world treats this as requiring empirical calibration, not prompt-engineering assurances. (Today we're safe by accident: one task per role. The A/B system is the controlled way to ever run two.)

### Recommendation — finish the pipeline with a battle-test stage and a report card

The review gate fails because approving a draft means reading hundreds of lines of generated JSON. Make the machine do the reading:

```
JD → generate (exists) → validate contract (exists) → BATTLE-TEST (new) → report card (new) → 1-click approve (exists-ish) → provision (exists) → calibration loop (schema exists)
```

**Battle-test stage** (automated, per draft, in E2B — this is exactly what was done by hand for tasks 36/37 and documented in the Phase-2 readiness review):
1. Materialize the repo; run bootstrap + test runner → baseline must fail meaningfully (N of M, not collect-error).
2. **Claude-alone lazy-operator run**: scripted minimal-effort operator ("fix it" × 3) against the task → its score is the difficulty floor. If lazy-Claude clears the tiers, the task can't discriminate and is auto-rejected. Re-run this baseline per candidate-model upgrade — Anthropic's core lesson is that this floor rises with every release.
3. Reference solve (Claude with a competent scripted operator) → confirms solvability inside the timebox.
4. Leak check: search public corpora for the scenario's distinctive strings.

**Report card** replaces raw-JSON review: one page — scenario summary, decision points, rubric weights by lens, baseline test state, lazy-floor score, reference-solve time, JD-coverage map. Surface it in the role's agent chat with approve / revise (revision loop already built). Target: a 2-minute recruiter decision. That unblocks the 10 stranded drafts and every future role.

**Calibration loop** (the `task_calibrations` table already exists): track per-task score distribution, completion rate, and time-use; flag outliers for revision; retire tasks whose lazy-floor has risen into the passing band. This is the lightweight IRT-ish loop the DET evidence says is the minimum bar for generated items.

Cost note: generation is ~$0.10–0.30/task and battle-testing adds a few sandbox-minutes + a couple of model calls — trivially cheap per role. Keep it triggered (role create / Workable sync / explicit ask), never sweeping; consistent with the no-auto-paid-jobs policy since activation stays human-gated.

---

## 5. Engagement — getting candidates to complete

### Our actual leak profile

From §1: of 34 real starts ever, 20 expired with work discarded (class fixed by #698), and the completions skew to instant-bailers — the 2026-06-25 diagnosis found **bimodal engagement: <1-minute bailers and deep workers**, all on working sandboxes. The welcome page is already right (explicit expectations, start gate — verified in code; no silent timer). The bail happens *inside the runtime, in the first minutes*. And the funnel above the runtime is dark: delivery tracking has never seen a real invite; preview→start is uninstrumented.

### What the research says (and how it maps)

- **The first 5–10 minutes are everything.** Modern Hire (~30M assessments): over half of dropout happens in the first 5–10 minutes regardless of total length; cutting 15 minutes of length buys only 1–2% completion ([summary](https://hrtechfeed.com/new-research-on-applicant-dropout-and-assessment-completion-rates/)). Our bimodal data agrees. So: don't shorten the task — fix the entry.
- **Job simulations complete +14% and candidates rate skills tests as fairer** (Modern Hire; TestGorilla 2024/25 reports). Pairing with Claude on a real incident *is* a job simulation — this is a completion asset to market, not just an assessment design.
- **Early abandonment is partly healthy self-selection** — chase the leak, not 100% completion.
- **Candidate resentment is at record highs (~25% in NA tech)** (Talent Board/CandE via [ERE](https://www.ere.net/articles/12-key-takeaways-from-the-2024-candidate-experience-benchmark-research)); giving something back to completers is the cheapest known antidote.
- **Payment:** no RCT exists; practitioner consensus is unpaid is fine at ≤90 minutes if the timebox is honest. Our 30-minute format is comfortably inside that.

### Recommendations, in impact order

1. **Restart sends.** Everything below is unmeasurable until invites flow. The stuck 06-25 batch has an operational tool ready (`backend/scripts/resend_failed_invites.py`); the GitHub-token watchdog (#701) plus `/healthz/github` protect the start path. This is the single highest-leverage action in this entire document.
2. **Instrument the dark segments.** Two events close the funnel: a `preview_viewed` timestamp (route exists, nothing recorded) and a first-5-minutes runtime event stream (first file opened, first prompt sent, first Claude response rendered). With delivery webhooks now capturing real sends, the full chain sent→delivered→opened→previewed→started→first-prompt→submit becomes visible. Also split `COMPLETED_DUE_TO_TIMEOUT` from `COMPLETED` in analytics (currently merged) — timeout-completion is a distinct engagement outcome.
3. **Engineer the first exchange.** The <1-min bailers met a full IDE and a blank chat. Use the existing `calibration_prompt` as a scripted, zero-stakes first exchange that Claude opens (not the candidate), demonstrating "ask me to look at the failing test" — an early win inside 2 minutes. The stage-stepper UI from #797 is a good affordance here even without two-stage scoring: a visible "1. Get oriented → 2. Fix the pipeline → 3. Submit" path kills blank-page paralysis.
4. **Nudge sequence off the webhook data we now get:** delivered-not-opened at 48h → one reminder; opened-not-started at 48h → a different reminder ("~30 minutes, pairs you with Claude, here's what to expect"); expiry reminder exists (7-day). All assessment-scoped (compliant with the no-job-emails policy).
5. **Give completers something.** A short auto-generated AI-fluency snippet ("what you did well pairing with Claude") emailed on completion. Costs one Haiku call; converts the assessment from extraction to exchange; directly addresses the resentment data. It's also a differentiator no vendor offers.
6. **Timebox honesty:** keep task design-time == invite window messaging consistent (the 30-min-design/90-min-window mismatch from June is the anti-pattern); "designed for 30 minutes, submit any time within 7 days" is the honest frame candidates reward.

---

## 6. Prioritised roadmap

| # | Action | Size | Depends on |
|---|---|---|---|
| **P0-1** | Resume invite volume (resend stuck batch; verify webhook events populate on first real send) | ops, hours | GitHub token healthy (watchdog live) |
| **P0-2** | Funnel instrumentation: `preview_viewed`, first-5-min runtime events, timeout-vs-clean completion split | S | — |
| **P0-3** | First-exchange redesign (Claude opens via `calibration_prompt`; stepper as orientation) | S–M | — |
| **P1-1** | Adopt discernment + diligence dims (from #716 lenses) on the 2 flagship data-eng tasks + 1 trap each; **shadow re-score** (`scripts/shadow_rescore_assessments.py`) before any live flip | S (task JSON) + shadow run | P0-1 for future data |
| **P1-2** | Deterministic process features at submit (`verification_events`, `refinement_ratio`, `challenge_events`, `deference_score`, server-validated cadence) → evidence panels + lens-prompt context | M | — |
| **P1-3** | Battle-test stage + report card for generated tasks; unblock the 10 stranded drafts; approve via agent chat | M | — |
| **P2-1** | Two-stage validation: observed-practice dims (10–15% weight) as default; ONE announced two-stage flagship task as an A/B arm; decide at ≥20 completions/arm | S + patience | P0-1, P1-3 |
| **P2-2** | Nudge sequence + completion feedback snippet | S | P0-1 (webhook data) |
| **P2-3** | Task calibration loop on `task_calibrations` (score distribution, lazy-floor drift per model release) | M | P1-3 |
| **P2-4** | Knowledge-work first-class: doc-task traps + claim-verification criteria; generate doc tasks for 2 non-eng roles | M | P1-3 |

**What NOT to do:** more proctoring signals; a 6th scorecard axis; blanket re-scores of historical assessments (per standing policy); trusting any scoring change without a shadow run; declaring two-stage dead *or* default before the A/B answers.

---

## Appendix — key sources

**Internal:** `backend/app/components/assessments/rubric_scoring.py` (lenses, part blend, 5-D rollup) · `submission_runtime.py` (authoritative score, part_scores) · `backend/app/services/task_spec_generator.py` + `task_provisioning_service.py` (JD→task autogen) · `backend/app/domains/assessments_runtime/candidate_claude_chat_routes.py` (per-turn capture, integrity) · `invite_flow.py` + `resend_webhook_service.py` (delivery tracking) · `frontend/src/shared/assessment/fluency4d.js` (scorecard) · prod DB queries 2026-07-10 (funnel, adoption) · PRs #716, #725, #746, #776, #797, #514, #698, #701.

**External (primary):**
- Anthropic — [AI Fluency framework](https://aifluencyframework.org/) · [candidate AI guidance](https://www.anthropic.com/candidate-ai-guidance) · [AI-resistant technical evaluations](https://www.anthropic.com/engineering/AI-resistant-technical-evaluations) · [Claude Code best practices](https://www.anthropic.com/engineering/claude-code-best-practices) · [Claude Code telemetry](https://code.claude.com/docs/en/monitoring-usage.md)
- Research — METR dev RCT ([arXiv:2507.09089](https://arxiv.org/abs/2507.09089)) · Dell'Acqua et al., Jagged Frontier ([SSRN 4573321](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4573321)) · Cybernetic Teammate ([SSRN 5188231](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5188231)) · LLM-as-judge ([arXiv:2306.05685](https://arxiv.org/abs/2306.05685), [arXiv:2510.12462](https://arxiv.org/html/2510.12462v3)) · Sackett et al. 2022 validity revision (via [SIOP](https://www.siop.org/tip-article/is-cognitive-ability-the-best-predictor-of-job-performance-new-research-says-its-time-to-think-again/)) · AutoIRT/Duolingo ([arXiv:2409.08823](https://arxiv.org/abs/2409.08823))
- Practice — [interviewing.io cheating study](https://interviewing.io/blog/how-hard-is-it-to-cheat-with-chatgpt-in-technical-interviews) · [CodeSignal AI-assisted assessments](https://codesignal.com/blog/introducing-ai-assisted-coding-assessments-interviews/) · Modern Hire completion research ([via HR Tech Feed](https://hrtechfeed.com/new-research-on-applicant-dropout-and-assessment-completion-rates/)) · [TestGorilla skills-based hiring reports](https://www.testgorilla.com/skills-based-hiring/state-of-skills-based-hiring-2025/) · Talent Board CandE ([via ERE](https://www.ere.net/articles/12-key-takeaways-from-the-2024-candidate-experience-benchmark-research))

Flagged as not-primary-verified by the research pass: Modern Hire's underlying report (press summary only), vendor completion benchmarks (directional), exact Sackett 2022 work-sample coefficient (paywalled), Karat's AI-assessment approach (no first-party doc).
