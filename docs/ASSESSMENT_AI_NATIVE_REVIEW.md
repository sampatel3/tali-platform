# Assessing AI-Native Ability — Deep Review & Refinement Plan

**Date:** 2026-06-25
**Author:** Claude (deep-research, 6 parallel streams: 3 internal codebase, 3 external/web)
**Scope:** (1) how a candidate interacts with Claude during a Tali assessment + what's appropriate to expose; (2) how we measure & score, vs. Anthropic's certifications/frameworks and the external state of the art. Goal: get Tali to correctly assess *AI-native ability* — coding or otherwise.

> **One-line verdict.** Tali is **ahead of the market on the hardest part** (it automatically *scores judgment / decision-ownership*, not just output — via the "interrogation" engine), but it is **blind to the single richest signal of AI-native skill: the process**. The grader never sees how the candidate actually drove the agent. Close that gap and re-base the vocabulary on Anthropic's verified **AI Fluency "4 Ds"**, and Tali becomes the most defensible AI-native assessment available.

---

## 0. TL;DR — the five findings that matter

1. **The crown jewel is real and rare.** The `interrogation` engine (`backend/app/components/assessments/interrogation.py`) makes the agent refuse to do load-bearing work until the candidate *commits to / reframes / dodges* each planted design decision, then scores that deterministically. This is the heaviest-weighted dimension (`design_decisions_articulated`, weight **0.40 / 0.35**) in **8 of 10** tasks. It directly implements what the research says matters most: *"interrogation beats deference"* and *score judgment, not output*. **Keep it; double down.**

2. **The process is invisible to grading — this is the #1 gap.** The LLM grader sees only `[Candidate]: message / [Claude]: response` text per turn ([`rubric_scoring.py:133-142`](../backend/app/components/assessments/rubric_scoring.py)). The agent's tool **results** are *discarded at the source* — only `{name, input}` is kept ([`claude_agent/service.py:322-323`](../backend/app/components/integrations/claude_agent/service.py)), and even that `tool_calls_made` list is never passed to the grader. Verification behaviour, iteration, catching the agent's mistakes, the read→edit→test→fix loop — none of it is captured-and-scored. The literature is unanimous that **this is exactly the difference between a strong and weak AI-native operator.**

3. **The harness is deliberately hobbled.** The candidate's Claude is **Haiku 4.5, pinned** ([`service.py:73`](../backend/app/components/integrations/claude_agent/service.py)), with **4 tools** (Read/Write/Edit/Bash, sandboxed), a hard **≤3 tool-calls-per-response cap** ([`candidate_claude_chat_routes.py:74-113`](../backend/app/domains/assessments_runtime/candidate_claude_chat_routes.py)), no web, no subagents, no plan mode, no skills, 30-minute clock. Good for cost/consistency/anti-cheat; **bad for ecological validity** — it tests steering of a *weak, throttled* model, not how a strong operator actually works.

4. **"Skills like goal / dynamicworkflow" don't exist** — and today the candidate can use **no** skills, subagents, or plan mode at all. The real design question isn't "which skills" but "**which capabilities' *use* is itself the signal we want, and can we capture + grade it?**" (Answer below: plan-before-build and controlled lookup are high-value; subagents/teams/SDK are out of scope for a 30-min screen.)

5. **There is a ready-made, Anthropic-blessed framework to re-base on.** Anthropic's **AI Fluency 4 Ds — Delegation, Description, Discernment, Diligence** (verified verbatim from Anthropic's CDN PDFs) is the only named, public, *measurable* model of human AI fluency, with **11 observable behaviours** operationalised in Anthropic's AI Fluency Index. Tali currently maps to *none* of it. Re-basing the scoring vocabulary on the 4 Ds gives external credibility, fairness defensibility, and a clean map of what to capture.

---

## 1. Current state — how the candidate interacts with Claude

**Environment.** Per-candidate E2B sandbox + a private GitHub repo per `task_key` (`org` default `taali-assessments`) + a per-candidate branch `assessment/<id>`, cloned into `/workspace/<task>`. In-browser Monaco editor + file tree; the terminal panel exists but is suppressed by default (and carries **no** Anthropic key — a candidate can't `echo $ANTHROPIC_API_KEY`).

**The AI surface.** *Not* Claude Code in the sandbox, *not* the raw API — it's the **`claude-agent-sdk` driven server-side through a Cursor-style chat panel** (`AssessmentClaudeChat.jsx` → `POST /assessments/{id}/claude/chat` → `AgentSDKChatService.run()`). The SDK spawns the bundled CLI as a subprocess that owns the inner tool loop; **one whole multi-turn tool loop is flattened to one `ai_prompts` record** per candidate message.

**Model.** `claude-haiku-4-5-20251001`, **pinned** (`_DEFAULT_AGENT_SDK_MODEL`, [`service.py:73`](../backend/app/components/integrations/claude_agent/service.py)). Swapped from Sonnet for latency (~3–5s vs ~30s). Note: this *bypasses* the pydantic `Settings.CLAUDE_CHAT_MODEL` (whose own default is the stale `claude-3-5-haiku-latest`).

**Tools exposed (exactly four, sandbox-scoped):** `mcp__sandbox__{Read,Write,Edit,Bash}` ([`claude_agent/sandbox_tools.py`](../backend/app/components/integrations/claude_agent/sandbox_tools.py)). `tools=[]` disables *all* SDK built-ins; `setting_sources=[]` blocks `~/.claude` leakage; `permission_mode="bypassPermissions"` (safe — tools touch only the isolated E2B VM). Bash has a blocklist (`sudo|doas`, `curl|wget|nc|ssh|scp|...`) but allows `pip/pytest/python/grep/git`.

**Hard limits / guardrails.**
- **≤3 tool calls advised, 4 hard** per response ("the 4th IS a failure" — in the system prompt); SDK `max_turns=25`; per-turn `max_budget_usd ≤ $1`.
- **30 minutes** total (auto-submit at 0; disposition `COMPLETED_DUE_TO_TIMEOUT`).
- **Integrity engine** (`integrity.py` + `scoring/rules.py`): always-injected `BOUNDARY_DIRECTIVE`; regex + semantic detection of prompt-injection, system-prompt/secret probing, and off-task asks (the agent emits an internal `[OFF_TASK_REFUSED]` marker); **warn at 2 flagged turns, hard-void at 3** (no score). Off-task/injection replies are overridden with a generic refusal so nothing leaks.
- **Client anti-cheat signals** captured (paste-detect, browser-focus, tab-switch, pacing) but only used when `proctoring_enabled` (per-task, default off; `MVP_DISABLE_PROCTORING=True`).

**A/B.** Experiments vary only **which task** the candidate gets (or knob overrides: duration / score weights / calibration). They **do not** change the Claude experience.

**What the candidate CAN do:** chat with Haiku; have it Read/Write/Edit/Bash inside their sandbox repo; iterate over turns; ask anything about the task.
**What they CANNOT do:** get a full solution handed over; reveal the system prompt/secrets; use web/subagents/plan-mode/skills/external-MCP/any Claude Code built-in; run network/privilege commands; exceed ~3–4 tool calls/response, 25 turns, the USD budget, or 30 minutes.

---

## 2. Current state — how we measure & score

**Pipeline.** `POST /{id}/submit` → `submit_assessment_impl` ([`submission_runtime.py`](../backend/app/components/assessments/submission_runtime.py)): run tests in sandbox → capture final repo files + `git_evidence` (diff/commits, computed once at submit) → compute heuristics → CV-fit match → **if the task has an `evaluation_rubric` + API key, `RubricScorer.grade_rubric` runs and its weighted score OVERRIDES the heuristic score** → blend into `taali_score`.

**Two grading engines coexist.** Legacy heuristic/regex scoring (`MVP_DISABLE_CLAUDE_SCORING=True` gates the old LLM path off; `code_quality_score` hardcoded 5.0). The authoritative path is **`RubricScorer`** (Sonnet 4.5, `temperature=0`, one call per dimension).

**Two grader "lenses"** (verbatim, [`rubric_scoring.py:199-227`](../backend/app/components/assessments/rubric_scoring.py)):
- **DECISION lens** — *"whether THE CANDIDATE made and owned the load-bearing calls… A candidate who delegated with 'fix it' / 'do all 3' and never engaged the decision scores POOR here regardless of how good the agent's output was."*
- **DELIVERABLE lens** — *"directing an agent to a correct, well-structured solution IS the skill being measured. DO NOT penalise the candidate for using the agent… If nothing coherent was shipped… that is POOR."*

**The interrogation grader (the novel core).** Each task carries `decision_points` (load-bearing design decisions with `ask`, `valid_commit`, `valid_reframes`, `anti_patterns`). At start the agent posts an opener refusing substantive work until the candidate commits. **Per turn**, a Haiku classifier labels the candidate's message `commit | reframe | dodge | vague | unaddressed` (reframes are first-class senior signals, *not* dodges). At submit the grader replays the accumulated state deterministically: any `dodge` → **1.5 (poor)**; all resolved → **9.5 (excellent)**; partial → `2.0 + (resolved/total)·5`. This dimension is weight **0.40/0.35** in 8/10 tasks.

**What the grader actually sees (`ScoringArtifacts`):** final repo files (≤20×6000 chars), test pass/fail summary, the **message+response transcript only** (≤20 turns × 2000 chars), task scenario, decision points. **It does *not* see:** tool calls, tool results, diffs, intermediate code, timing, or any process telemetry.

**Dimensions.** Not a canonical 6 — **27 task-specific dimensions** across 10 specs, unified by a per-task *pattern*: one decision-lens *diagnosis* dim (~0.2) + `design_decisions_articulated` (0.4, interrogation) + 2–3 deliverable-lens correctness dims. The **2 platform-eng tasks (EKS/AKS) are outliers** — no interrogation, no lens, no `decision_points`; 5 plain LLM-judged criteria.

**Presentation.** The candidate report rolls these into **6 "fluency" axes** (`shared/assessment/fluencyRollup.js`): *Systems design, Code craft, Reasoning under pressure, AI collaboration, Release safety, Communication* (this is memory's "6 axes"), plus an 8-dimension radar and a ~30-metric glossary. AI-process detail (per-prompt clarity/specificity/efficiency, time-to-first-prompt, code replay, git evidence) is shown to recruiters but **is not in the authoritative score**.

**Capture vs. graded:**

| Signal | Captured? | Fed to grader? |
|---|---|---|
| Final repo files | ✅ | ✅ |
| Test pass/fail summary | ✅ | ✅ (as text) |
| Chat transcript (message+response) | ✅ | ✅ |
| Per-decision interrogation state | ✅ | ✅ (deterministic) |
| **Agent tool *calls* (`{name,input}`)** | ✅ | ❌ analytics-only |
| **Agent tool *results*** | ❌ **discarded at source** | ❌ |
| **Git diff / commits** | ✅ (at submit) | ❌ recruiter-only |
| Intermediate code states | ❌ (near-empty on agentic path) | ❌ |
| Keystrokes | ❌ (`AssessmentSession` table is dead) | ❌ |
| Timing / paste / focus / tab-switch | ✅ | ❌ (fraud flags only) |

---

## 3. External benchmark — certifications, frameworks, state of the art

### 3.1 Anthropic certifications — what they test, and is it "covered"?
- **Anthropic Academy** (free courses + completion certificates) and the **Claude Certified Architect — Foundations (CCA-F)** exam (launched Mar 2026 — Anthropic's first professional cert). CCA-F domains (third-party-reported, *unconfirmed by Anthropic*): Agentic Architecture & Orchestration 27% · Claude Code Config & Workflows 20% · Prompt Engineering & Structured Output 20% · Tool Design & MCP Integration 18% · Context Management & Reliability 15%.
- **Is Tali covered by / aligned with CCA-F? No — and that's fine, it's a *different construct*.** CCA-F certifies *building production Claude systems* (SDK/MCP/Claude Code). Tali assesses *working effectively with AI on the job for a role*. Tali touches **none** of the CCA-F domains directly. CCA-F is **not** a template for Tali — but its domains hint at what "advanced AI-native engineering" looks like (orchestration, prompt/context discipline, reliability), which reinforces the gaps in §4.
- **Crucial gap on Anthropic's side:** Anthropic publishes **no instrument for assessing AI skill in *individuals*** — their "evals" are for *models*; their AI Fluency Index measures behaviours *in aggregate*. **There is an open space for Tali to be the credible per-person AI-fluency assessment.**

### 3.2 The framework to adopt — Anthropic's "AI Fluency 4 Ds" (verified verbatim)
- **Delegation** — *"Setting goals and deciding whether, when and how to engage with AI."* (Problem / Platform / Task awareness.)
- **Description** — *"Effectively describing goals to prompt useful AI behaviors and outputs."* (Product / Process / Performance description = prompt + context engineering.)
- **Discernment** — *"Accurately assessing the usefulness of AI outputs and behaviours."* (Product / Process / Performance discernment = catching bad output & flawed reasoning.)
- **Diligence** — *"Taking responsibility for what we do with AI and how we do it."* (Creation / Transparency / Deployment = verification + ownership.)
- 3 interaction modes: **Automation / Augmentation / Agency.** 11 of 24 behaviours are *directly observable* in a human↔Claude session (the operational, measurable seam).

### 3.3 State of the art in AI-native assessment (2025–2026)
- **Market posture has flipped** from "ban AI" to "**allow AI, observe the collaboration, score the judgment.**" Vendors: **CodeSignal** (agentic assessments — build from ambiguous reqs with Claude Code/Cursor, **full transcript + session replay**, then explain decisions), **HackerRank** (AI Interviewer + Proctor replay), **CoderPad** (Ask/Edit/Plan modes, interviewer sees prompt history), **Codility** (AI Copilot, enforce model/mode), **Karat** ("NextGen" human-led + AI-enabled). Companies: **Canva** *requires* AI and added an **"AI-Assisted Coding" competency** — and found candidates failed *"not because they couldn't code, but because they lacked the judgment to guide AI effectively"*; **Shopify** wants 90–95% AI use *with* the ability to fix its errors; **Sierra**'s onsite is a **Plan → Build → Review** loop scored on technical judgment + AI usage; **Anthropic itself does *not* allow AI in its own hiring assessments** (instructive: for some stages you must measure the unaided human).
- **What predicts strong AI-native performance** (convergent across DORA 2025, METR, HCI research, practitioners):
  - **AI is an amplifier** (DORA 2025, verbatim): *"AI doesn't fix a team; it amplifies what's already there."* Raises the ceiling for the disciplined, lowers the floor for the weak.
  - **Tight verification loop** — treat AI output as a draft from a junior; *"verification, not generation, is the new bottleneck."* **Can explain every committed line** (Willison's rule).
  - **Plan before generating**; **instrument with tests/linters** ("sensors"); **manage context deliberately**; **know when to abandon the AI approach** (experts accepted <44% of generations in METR).
  - **Appropriate reliance** (HCI): operationalised as **RAIR** (correctly switching *to* good AI advice) and **RSR** (correctly *rejecting* wrong AI advice). *Trust ≠ reliance*; self-reported confidence does **not** predict appropriate reliance — you must test *verification*.
  - **Weak operators**: accept blindly (automation complacency — in one study, a run of valid edits lulled devs into accepting a *final insecure* suggestion **69%** of the time), ship "house-of-cards" code / "workslop," and **mis-judge their own productivity** (METR: devs felt ~20% faster while ~19% slower).
- **Benchmarks (SWE-bench, Terminal-Bench) measure *models*, not people** — using a benchmark score to grade a *person* is a category error (the same model swings 20+ pts on Terminal-Bench by harness alone; OpenAI stopped reporting SWE-bench Verified over contamination). Use them to pick *which model you let candidates use*, not to score candidates.
- **Pitfalls + mitigations:** output-only assessments are broken (verbatim LeetCode cheats pass 73%, custom 25%, 0/32 interviewers noticed) → **custom, multi-file, ambiguous tasks that can't be one-shot-prompted**; output-vs-process confusion (judging finished code is *"a coin flip"*; watching the *process* makes skill clear) → **capture & score process + a live "explain your decisions" defense**; proctoring harms + identity fraud (Gartner: ~1 in 4 applicant profiles fake by 2028) → many firms re-add a live human-observed round; over-indexing on one model's quirks → **assess durable judgment, let candidates pick tools, refresh tasks**.

---

## 4. Gap analysis — what we measure vs. what predicts AI-native ability

| AI-Fluency dimension (Anthropic 4 Ds) | What predicts skill (research) | Tali today | Verdict |
|---|---|---|---|
| **Delegation** | Decompose; decide what to hand to AI vs. own; goal/platform awareness | **Strong** — interrogation engine forces decision-ownership; lazy "do it all" punished | ✅ Best-in-class. Keep. |
| **Description** | Prompt + context engineering quality | Heuristic clarity/specificity exist but are **overridden** by the rubric; not authoritatively scored | ⚠️ Captured, not graded |
| **Discernment** | Catch the AI's mistakes; reject bad output (RSR); appropriate reliance | Only indirectly via interrogation; **no planted-error / "did they catch it" mechanic**; grader can't see what the agent actually did | ❌ Major gap |
| **Diligence** | Verify, test, take ownership of shipped output | Deliverable lens credits a *working* result, but **verification *behaviour* during the session is not scored**; tool results discarded | ❌ Major gap |
| **Process visibility** (cross-cutting) | Watching *how* they work is the only reliable read | Grader sees message+response text only; tool results never captured; diffs not fed in | ❌ The #1 gap |
| **Ecological validity** | Realistic tools/model; their own workflow | Pinned **Haiku**, 4 tools, ≤3 calls/response, no plan/web/subagents | ⚠️ Standardised but unrealistic |
| **Framework/defensibility** | Named, fair, auditable construct | Bespoke 27-dim / 6-axis vocabulary, mapped to no external standard | ⚠️ Credibility/fairness risk |

**Net:** Tali measures AI-native ability as **(a) decision ownership** (excellent, novel) + **(b) artifact correctness** (fine). It under-measures **Description** and badly under-measures **Discernment** and **Diligence** — the verification/oversight half of the 4 Ds that the research says best separates strong from weak operators — *because the process is invisible to the grader.*

---

## 5. Refinements — prioritised

### Tier 0 — Unlock process-based scoring (days; high leverage, low risk)
These are prerequisites for everything else and are nearly free.

- **T0.1 — Capture tool *results*, not just calls.** Today `claude_agent/service.py:322-323` keeps only `{name,input}` and throws away what `Read` returned / `Bash` printed / whether the `Edit` applied. Persist a bounded result per tool call on the `ai_prompts` record. *Without this, "did they verify / catch the bug" is unknowable.*
- **T0.2 — Feed the process trace to the grader.** Extend `prompt_transcript_excerpt` (`rubric_scoring.py:133-142`) to render an interleaved trace: candidate message → agent tool calls **+ results** → agent text. Add `git_evidence` (already captured) to `ScoringArtifacts`. This single change lets the *existing* lenses reason about process.
- **T0.3 — Rethink the ≤3-tool-calls-per-response cap.** It's an artificial throttle that distorts how candidates work (a real agentic step often needs read→edit→test). Replace the hard "4th = failure" rule with a per-turn token/$ budget (already enforced) and a soft nudge. Keep latency acceptable by relaxing, not capping.
- **T0.4 — Fix the EKS/AKS outliers** to the catalog standard (add `decision_points` + lenses) so all tasks score on the same construct.

### Tier 1 — Score the verification/oversight half (weeks)
- **T1.1 — Add explicit graded dimensions for Discernment & Diligence**, now that the trace is visible: *did the candidate test / run the code, inspect outputs, catch & correct an agent mistake, reject a bad suggestion?* These map to the RAIR/RSR "appropriate reliance" constructs.
- **T1.2 — Plant a wrong-but-plausible AI suggestion or a latent bug** in the starter repo / likely agent path, and score whether the candidate catches it (direct RSR test). This is the highest-signal, hardest-to-game Discernment probe and aligns with the "planted bug code-review" pattern leading firms use.
- **T1.3 — Promote Description from heuristic to graded** — prompt/context quality as a real dimension (clarity, decomposition, context provided), not just radar telemetry.
- **T1.4 — Re-base the scoring vocabulary on the AI Fluency 4 Ds.** Map the 6 rollup axes and 27 dims onto Delegation / Description / Discernment / Diligence (+ a deliverable-correctness axis). Gives a named, defensible, Anthropic-aligned construct — important for a hiring product (fairness/audit/legal) and a marketing asset ("assessed on Anthropic's AI Fluency framework").

### Tier 2 — Ecological validity (weeks–months; the strategic part)
- **T2.1 — Upgrade / configure the candidate model.** Steering a hobbled Haiku under-represents a strong operator's skill at directing Sonnet/Opus. Options: bump to Sonnet for the build; or let the *candidate* pick the model (and *score the choice* — Platform Awareness under Delegation). Keep the interrogation classifier + grader on fixed models for consistency.
- **T2.2 — Selectively expose high-signal capabilities** (capture + score their use): **plan-before-build** (the strongest research-backed behaviour; the plan is text, trivially graded), and **controlled doc/web lookup** (real engineers look things up). *Defer* subagents / agent-teams / SDK / custom-MCP — too expensive, hard to grade, and not diagnostic for a 30-min role screen.
- **T2.3 — Optional async "explain & defend your decisions" step** (the market's consensus 5th stage). A short recorded/written reflection ("where did AI help, where did you override it, what did you verify?") graded by LLM — adds the human-defense signal without breaking automation.

### Tier 3 — The strategic fork (decision required — see §6)
Standardised constrained harness **vs.** bring-your-own-tools realism.

### Cross-cutting
- **Anti-amplifier discrimination:** keep tasks ambiguous with planted contradictions (already strong) so the floor/ceiling spread shows.
- **Self-perception probe:** capture a candidate self-estimate vs. measured outcome — the gap is itself a validated weak-operator signal.
- **Anti-gaming hygiene:** tasks are custom/ambiguous (good); refresh on a cadence; don't tune questions to defeat *today's* model.

---

## 6. The strategic fork

The whole overhaul hinges on one choice:

**Option A — Standardised harness + process capture + realism (recommended).** Keep the in-platform, instrumented chat (it's the moat: consistent, automatable, defensible, anti-cheat). Invest in Tier 0–2: make the process visible to grading, score the verification half, re-base on the 4 Ds, upgrade the model, add plan-mode + lookup. *Pros:* preserves Tali's automated, comparable scoring; closes the real gaps. *Cons:* still not "their real laptop."

**Option B — Bring-your-own-tools.** Let candidates use Claude Code / Cursor on a realistic repo, capture everything, score process + a live defense. *Pros:* maximal realism; matches CodeSignal/Sierra. *Cons:* breaks standardisation & automated comparability (the moat), expensive, anti-cheat much harder.

**Option C — Hybrid.** Standardised harness for the scored core **+** a short BYO or live "build & defend" segment for senior roles.

**Recommendation: A now, C later for senior roles.** Tali's differentiator is *automated, consistent, defensible judgment scoring* — Option B trades that away to chase realism that Tier 2 can mostly buy. Do A (Tiers 0–2), revisit C once the process-scoring foundation is proven.

---

## Appendix — key sources
**Internal:** `interrogation.py`, `rubric_scoring.py`, `submission_runtime.py`, `claude_agent/service.py`, `candidate_claude_chat_routes.py`, `backend/tasks/*.json`, `shared/assessment/fluencyRollup.js`, `scoring/scoringDimensions.ts`.
**Anthropic frameworks:** AI Fluency 4 Ds (anthropic.com/learn; CDN PDFs); AI Fluency Index (anthropic.com/research/AI-fluency-index); Claude Partner Network / CCA-F (anthropic.com/news/claude-partner-network); building-effective-agents & context-engineering posts.
**SOTA / research:** DORA 2025 ("AI is an amplifier"); METR uplift RCT + Feb-2026 revision; Schemmer et al. RAIR/RSR (IUI 2023); Fok & Weld "In Search of Verifiability"; Gonzalez et al. PNAS Nexus 2026; Canva / Sierra / Shopify / Anthropic hiring posts; CodeSignal / HackerRank / CoderPad / Karat product docs; SWE-bench / Terminal-Bench + "Coding Benchmarks Are Misaligned with Agentic SE" (arXiv 2606.17799).
