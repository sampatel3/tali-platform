# Taali Pre-Screen Scoring & Integrity Stage — Engineering Deep-Dive

*For Sam. Backend is Python/FastAPI/SQLAlchemy/Celery. All paths are under `backend/`.*

---

## 0. Production-grounded addendum (verified 2026-06-14)

The report body below was written from the repo, which carries conservative defaults. I read the **live Railway config** to ground it. The truths that differ from the defaults:

| Setting | Repo default | **Live prod** | Note |
|---|---|---|---|
| `ENABLE_PRE_SCREEN_GATE` | `False` | **`true`** (API + scoring worker) | The gate is ON. |
| `PRE_SCREEN_THRESHOLD` | `30` | **`50` on the API, UNSET (→30) on both workers** | ⚠️ **Per-service drift** — see below. |
| `HOLISTIC_SCORING_ENABLED` / `_ORG_IDS` | `False` / `""` | **`true` / `2`** (scoring worker) | Holistic Sonnet is LIVE for **org 2** — so the A7 silent-zero bug is live. |
| `CLAUDE_MODEL` / `CLAUDE_SCORING_MODEL` | haiku / "" | `claude-sonnet-4-5` | Agent/chat on Sonnet; the pre-screen gate still pins Haiku (`FAST_MODEL`) regardless. |

**⚠️ Threshold drift (new finding, only visible from prod).** The autonomous scoring funnel runs in the Celery **`taali-worker-scoring`** service, where `PRE_SCREEN_THRESHOLD` is **unset → code default 30**. The API (`resourceful-adaptation`) has it at **50**. So the same candidate is gated at **30 by the autonomous loop** and **50 by manual/API-triggered scoring** — a real inconsistency (memory's per-service-env-drift hazard). Raising the worker to 50 rejects more candidates; this is a **volume/policy decision**, left for Sam — I did not change prod env.

### What shipped in this PR (provably never-worse)
- **A7 fix** — `_LeanScore.overall` made **required** (`holistic.py`). Previously a degraded-but-schema-valid Sonnet tool emission that omitted `overall` validated as `overall=0` → `cv_match_score=0` with status OK = a **silent 0-score auto-reject of a real candidate, live on org 2**. Now an absent field raises in the structured layer's `model_validate` → `ValidationFailure` → retry-with-feedback → only-if-still-missing `FAILED` (→ `cv_match_score=None`, retried later — never a 0 auto-reject). A genuine model-emitted `overall=0` (real clear-misfit) still passes. Verified: the FAILED branch (`cv_score_orchestrator.py:743`) sets `cv_match_score=None`, never persisting the 0. Regression test added; holistic/orchestrator/runner/qa/gateway/arch suites green.

### Deliberately NOT auto-shipped (would change live verdicts for org 2 or are policy calls — would violate "don't break prod")
- **R1** (sub-agent omits must-haves) — fixing it re-scores the autonomous pre-screen path; bounded by the fast-path but still a live behaviour change → validate first.
- **R5** (holistic skips the timeline/unverified-claim integrity penalties) — porting them lowers some org-2 scores and could flip send→reject → Sam's open question #2 (intentional?).
- **Threshold drift** (above) — env/policy decision.
- **Integrity-axis framework + measurement (shadow-eval, adverse-impact monitor) + new modules** — the P1/P2 roadmap; build behind shadow + the bias monitor, not blind.

---

## 1. Executive summary

- **What the gate is.** Stage-1 is a cheap, permissive Haiku filter (`runner_pre_screen.py`, prompt `cv_pre_screen_v2.3`) that runs *inside* the async CV-scoring job, immediately before the expensive full score. One call, `claude-haiku-4-5`, `max_tokens=256`, temp 0, ~$0.0002–0.0015/CV. A deterministic CV↔JD copy-paste fraud check runs *before* the LLM and short-circuits plagiarised CVs for free. Survivors flow to either Haiku v18 full scoring (default orgs) or the Sonnet holistic engine (org 2). The whole thing is a textbook cheap→expensive cascade.

- **It is two paths, not one, and the split is load-bearing.** The default-org path full-scores everyone with Haiku v18 (0.40·cv_fit + 0.60·graded-requirements − bounded integrity penalty). The org-2 path routes survivors to a two-call Sonnet "holistic" engine (`holistic.py`, engine v2.1.0) where the model's `overall` becomes `role_fit_score` directly. **Critically, the holistic engine applies *none* of the timeline/unverified-claim integrity penalties** — for the flagship org, the CV-integrity layer is dead code.

- **How good it is today — accuracy.** Unknown, and structurally unmeasurable. The gate's LLM yes/no decision quality is never exercised by any test (every test injects a fixed score). There is **no labelled ground truth** — `golden_cases.yaml` is a single synthetic placeholder. The one standing accuracy instrument (`pre_screen_gate_divergence_report`) **cannot observe a single gate-filtered candidate**, because the gate NULLs `cv_match_score` on exactly the population it rejects. The most costly error class — the false reject — is invisible to its own monitor.

- **How good it is today — cost.** The per-call gate is well-disciplined (prompt-cached system block, DB cache, free fraud short-circuit). But the gate defaults **off** (`ENABLE_PRE_SCREEN_GATE=False`), so the always-on scoring funnel pays full price on every candidate — worst case for org 2, the costliest engine, with no cheap pre-filter. The **Sonnet holistic engine uses no Anthropic prompt caching** despite both Haiku paths using it, re-sends the CV twice per candidate at full rate, and fires a 5000-token report even on clear rejects. A built, tested **50%-discount Batches runner is never called in production.**

- **How good it is today — speed.** Adequate for the per-candidate gate, but the autonomous funnel drains at **50 candidates/hour/role** (per-tick cap × 60-min beat) with no fast lane for the cheap gate; on a tens-of-thousands backlog that is days. Manual batch pre-screen runs **single-threaded in-process on the web server**, so the cheap filter is delivered more slowly across a cohort than the expensive one.

- **The 3–4 biggest problems.** (1) **Fairness/legal exposure on a live reject path** — no adverse-impact monitoring on the gate/auto-reject, protected-class proxies in the raw text the model reads, a deterministic 8-gram heuristic that brands legitimate JD-mirroring candidates "potential fraud," and no candidate notice/explanation/appeal. (2) **A silent false-reject bug** — the holistic engine returns `role_fit_score=0` on a degraded-but-schema-valid LLM response, auto-rejecting a real candidate. (3) **Path divergence** — the sub-agent pre-screen omits must-haves and resolves a different JD, so it scores the same candidate differently and can't share cache. (4) **No feedback loop** — the threshold (30) is asserted, not validated, and the data pipeline to validate it (`prescreen_calibration_samples`) collects samples that nothing consumes.

- **Headline recommendations.** Treat the gate + auto-reject as the AEDT-of-record. Lead with the cheap, high-leverage wins: fix the holistic-zero false-reject, fix the sub-agent divergence, stand up an adverse-impact monitor and a shadow-eval that full-scores a sample of *filtered* candidates so false rejects become measurable. Then turn the gate on deliberately behind a calibrated borderline band, add prompt caching + the report skip + batching to the Sonnet engine, and reframe copy-paste from a hard "fraud" cap to a soft HITL flag. **Build a unified, separately-surfaced integrity *axis* (never silently mutating the fit score); never gate or auto-reject on AI-CV detection — it is biased against non-native speakers and trivially evaded.**

---

## 2. How it works today

### 2.1 The live flow (and the two-path situation)

A `CvScoreJob` is enqueued (`cv_score_orchestrator.enqueue_score`) from one of: the agent cohort tick (`agent_tasks._auto_enqueue_scoring`, only for `agentic_mode_enabled` un-paused roles), recruiter single/batch/process routes, or `sweep_stale_scores`. Celery `score_application_job` → `cv_score_orchestrator._execute_scoring_v3` (`cv_score_orchestrator.py:478`).

**Gate branch** (`cv_score_orchestrator.py:555`):
```
if settings.ENABLE_PRE_SCREEN_GATE and not force_full_score:
    if application_needs_pre_screen(app):
        execute_pre_screen_only(app, db, client)
```
`execute_pre_screen_only` (`pre_screening_service.py:192`) runs the deterministic copy-paste detector **first** (`:280`); on trigger it caps to 10 and returns with no LLM call. Otherwise `run_pre_screen` (`runner_pre_screen.py:185`) does one Haiku call returning `{score 0-100, reason, unverified_extraordinary_claim}`, `decision='yes' if score>=50 else 'no'` (`:317`). Soft penalties apply (`apply_fraud_penalty`, `apply_unverified_claim_prescreen_penalty`), and the run persists `pre_screen_score_100`, `genuine_pre_screen_score_100`, and `pre_screen_evidence['llm_score_100']` (the raw pre-penalty score).

**Gate decision** (`cv_score_orchestrator.py:566-664`): `gated_score = FRAUD_PENALTY_CAP_SCORE if fraud_capped else evidence['llm_score_100'] else application.pre_screen_score_100`. If `gated_score < PRE_SCREEN_THRESHOLD` (30) → write `cv_match_details` with `pre_screen_filtered`, set `cv_match_score = None` (`:647`), and **return without full scoring**. A pre-screen `error` hard-stops the job (`SCORE_JOB_ERROR`) with a 6h backoff.

**Survivors / gate-off** (`cv_score_orchestrator.py:700`): `if _holistic_enabled_for(app)` → `run_holistic_match` (Sonnet) else `run_cv_match` (Haiku v18). `cv_match_score = output.role_fit_score`. A post-commit step dispatches `run_application_auto_reject` for filtered candidates → Workable disqualify (if `role.auto_reject` + eligible) or a Decision-Hub HITL card.

| | Default org | Org 2 (flagship) |
|---|---|---|
| Pre-screen gate engine | Haiku `cv_pre_screen_v2.3` | Haiku (same) |
| Full-score engine | `run_cv_match` Haiku v18 | `run_holistic_match` Sonnet v2.1.0 |
| role_fit derivation | 0.40·cv_fit + 0.60·graded-req − integrity penalty | `overall` → `role_fit` directly (no aggregation) |
| Timeline/unverified-claim integrity penalty | **Applied** (`runner.py:380-387`) | **NOT applied** (`holistic.py` imports no fraud) |
| Prompt caching | Yes (cache_control) | **No** |
| Score-call count | 3 Haiku | 2–3 Sonnet |

### 2.2 Engines and score scales

- **Pre-screen (cheap floor, permissive):** instructed to score ≥60, "default 70 when uncertain," only <30 for obvious misfits, **must-haves only.** Its purpose is a cheap floor, not a predictor.
- **Holistic (decisive, full-range):** told to "use the FULL range, do NOT cluster" — 75–100 strong / 35–54 weak / 0–30 clear misfit.
- **The two 0-100 scales are not calibrated to each other** and are not directly comparable. They are *meant* to agree only on pass/no-pass intent.

**Three (really four) cutoffs over the same pre-screen score:** the runner sets `decision='yes'` at ≥50 (`runner_pre_screen.py:317`); the orchestrator gate filters at <30 (`PRE_SCREEN_THRESHOLD`); the recruiter label uses 80/65/50 bands (`pre_screening_snapshot.py:54-63`); the auto-reject card keys off `role.score_threshold` (a fourth value). A 35-scorer is a runner "no," a gate "keep," and an ambiguous label simultaneously.

### 2.3 Fraud/integrity signals that exist today

- **CV↔JD copy-paste** (`fraud_detection.py:74`, deterministic 8-gram overlap): triggers at ≥5% overlap, hard-caps to 10 (below the 30 gate), brands "Flagged for potential fraud."
- **Timeline inconsistencies** (`fraud_detection.py:330`): future dates, end-before-start, impossible spans, >2 concurrent current roles. Bounded soft penalty.
- **Unverified extraordinary claim** (`fraud_detection.py:389-395`): penalises only when uncorroborated AND model-unfamiliar. Soft −5 nudge.
- **Integrity penalty** (`compute_integrity_penalty`, capped at 15): never single-handedly rejects.
- **Tokenizer is `[a-z0-9]+` only** (`fraud_detection.py:33`) — non-Latin (Arabic/Chinese/Cyrillic) CVs tokenize to ~zero and silently pass every CV-fraud check.

### 2.4 Threshold & calibration

Two thresholds: a global Stage-1 filter (30) and a per-role reject-card threshold. A nightly Youden-J learner writes shadow ("proposed") `ThresholdCalibration` rows that are inert until manual activation; auto-apply is off by default and bias-gated against an **empty holdout** (`config/bias_audit_examples/` has only `README.md`). A weekly shadow-sampler (`prescreen_calibration.py`) full-scores a random sample of below-threshold rejects into `prescreen_calibration_samples` — **but nothing reads that table** (the producer exists, the part-B consumer/calibrator does not).

### 2.5 Cost & latency per candidate

- **Pre-screen:** ~$0.0015 warm (cache-read), ~$0.0046 cold (cache-write), $0 on DB-cache hit or deterministic fraud short-circuit. Metered `Feature.PRESCREEN` (1.0× markup; `Feature.SCORE` is 3.0×). Sub-second to ~2s, inside the Celery job (off the request path). For sparse roles (1–2 candidates/hour) the 1h cache TTL can cost *more* than no caching.
- **Full score:** Haiku v18 ~10× the pre-screen tokens; Sonnet holistic = two Sonnet calls (score 2000 + report 5000 max-tokens) + a Redis-cached derivation call, no prompt caching.
- **Reconciliation split:** pre-screen ~6% of spend, score ~43% — consistent with the gate being driven by recruiter batch actions, not the always-on funnel.

---

## 3. Evaluation — confirmed findings by dimension

### 3.1 Accuracy & correctness — *lead: the asymmetric false-reject risk*

For an asymmetric-cost gate, a false reject is the catastrophic error — it terminates a candidate with no second look. The standing instrumentation is structurally blind to exactly this class, and one live bug produces it silently.

| # | Finding | Sev | Location | Fix |
|---|---|---|---|---|
| A1 | **The only gate-accuracy monitor cannot observe a single autonomously-filtered candidate.** `pre_screen_gate_divergence_report` filters `cv_match_score.isnot(None)`, but the gate sets `cv_match_score=None` on every candidate it rejects. The `false_neg` band (`llm<30 and cv>=50`) can only fire for `force_full_score` manual rescores — never for the autonomous filter path. Do **not** read a zero count as "the gate is safe." | medium | `pre_screen_decision_emitter.py:1262-1282` vs `cv_score_orchestrator.py:647` | Shadow-score a sample of *filtered* candidates and compare; or persist the would-be full score in a side column. |
| A2 | **Divergence bands hard-code 30/50,** ignoring `PRE_SCREEN_THRESHOLD` and the per-role cutoff. If an operator retunes the threshold, the one instrument meant to validate the change silently keeps measuring the old value. | medium | `pre_screen_decision_emitter.py:1279-1281` | Parameterize bands on `settings.PRE_SCREEN_THRESHOLD` (and per-role threshold). |
| A3 | **The default threshold 30 has no data justification** — a one-line config comment, no ground-truth eval, untested LLM decision quality, a single synthetic golden case. (Bounded by the fact that the shadow *collection* pipeline exists, but nothing consumes it.) | low | `config.py:366-371`; `cv_score_orchestrator.py:566,618` | Run a shadow eval at 30/25/20 on filtered candidates; keep conservative until measured; wire a consumer for `prescreen_calibration_samples`. |
| A4 | **Three/four disagreeing cutoffs over one score.** A 35-scorer is runner-"no", gate-"keep", ambiguous label. The genuine hazard: for a role with `score_threshold>30`, the *pre-gate* auto-reject path can reject a 35-scorer the gate deliberately passed (the numeric guard `35>=45` is False). Protected once full scoring completes; the window is the cheap pre-gate path. | medium | `runner_pre_screen.py:317`; `cv_score_orchestrator.py:618`; `pre_screening_snapshot.py:52-63`; `auto_reject.py:100,126,149` | Unify the cutoff vocabulary; stop treating the runner's ≥50 decision as a gate; ensure auto-reject can't reject a 30–49 scorer the gate passed. |
| A5 | **Gate scores untruncated CV/JD; holistic truncates to 14k/8k.** The two engines judge different input windows; the genuine false-reject path is decisive must-have evidence past char 14000 the holistic engine never sees. | medium | `runner_pre_screen.py:251-252` vs `holistic.py:89-91,370,382` | Align the gate's input window to the holistic limits, or remove the holistic truncation. |
| A6 | **The −5 unverified penalty is invisible to the gate, and the durable penalized column is read by neither the gate nor the card.** Three scores exist (raw `llm_score_100`, penalized `genuine_pre_screen_score_100`, snapshot `pre_screen_score`); the integrity nudge is inert on every decision path. (Harm direction is leniency, not false-reject.) | medium | `cv_score_orchestrator.py:576-582`; `pre_screening_service.py:369-421`; `pre_screening_snapshot.py:127-142` | Decide which score is authoritative per decision and use it consistently; make the snapshot read `genuine_pre_screen_score_100`. |
| **A7** | **HIGH — Holistic returns a silent `role_fit_score=0` on a partial tool emission instead of FAILED.** `_LeanScore.overall` has `default=0`; a degraded-but-schema-valid Sonnet response (e.g. truncation that omits `overall`) validates `ok=True`, sets overall=0, and the orchestrator persists `cv_match_score=0` with `scoring_status=OK` — an auto-reject by parse-degradation on a real candidate, complete with a plausible-looking verdict/reasoning. Live on org 2. | **high** | `holistic.py:153,545,564-566`; `structured.py:356-358,392-399` | Make `overall` required (no default), or add a semantic validator rejecting missing/zero `overall` when verdict/reasoning are present, so it returns FAILED (which the orchestrator already NULLs and retries). |
| A8 | **Stale docstrings.** Runner says error "falls through to v3"; it now hard-stops with a 6h backoff. Prompt says threshold default 40; it is 30. A maintainer would mis-tune both. | low | `runner_pre_screen.py:6-7`; `prompts_pre_screen.py:4-6`; `cv_score_orchestrator.py:590-616` | Update docstrings; consider a shorter backoff for transient errors. |

**A7 is the priority correctness fix** — it is the only confirmed *live* silent false-reject in the scoring path.

### 3.2 Cost-efficiency

| # | Finding | Sev | Location | Fix |
|---|---|---|---|---|
| C1 | **Cheap gate off by default** → the funnel pays full price on every candidate; worst case for org-2 Sonnet. (Cost-only; off-by-default is documented as intentional; live Railway value unverified.) | medium | `config.py:353`; `cv_score_orchestrator.py:555` | Confirm the live value; enable behind a calibrated band; document the flag in `.env.example`. |
| **C2** | **HIGH — Holistic Sonnet uses no prompt caching** while both Haiku paths do. The CV+reqblock+workable is re-sent twice per candidate at full Sonnet rate (3× Haiku; cache-read would be 0.10×). The most expensive engine is the only one without caching. | **high** | `holistic.py:368-385,197,217` | Hoist shared per-role content (core + reqblock) into a leading `cache_control` ephemeral block reused across both Sonnet calls, mirroring `prompts.py`. |
| C3 | **Holistic report (5000 max-tokens) runs unconditionally,** even for 0–30 clear-misfit rejects. (Low because it's wasted spend on rejects, not a correctness harm, and the report has audit value; only bites when the gate is off.) | low | `holistic.py:379-386` | Gate call-2 on the overall score; skip/shrink the report below a clear-reject band. `_to_output` already tolerates an empty `_Report`. |
| C4 | **The 50%-discount Batches runner is built, tested, metered — and never called.** Bulk scoring and the org-2 366-app backfill paid full price. (No Sonnet batch path exists, so org 2 couldn't use it even if wired.) | medium | `runner_batch.py:117,590`; `scoring_tasks.py:171` | Route non-interactive bulk/backfill scoring through `run_cv_match_batch`; consider a Sonnet batch path for recurring holistic backfills. |
| C5 | **Cache key includes `workable_context` but the re-run trigger does not.** Activity-log churn busts the holistic cache (a bust = two full Sonnet calls) while the gate's staleness check never reacts to context changes. | low | `cache.py:51`; `runner_pre_screen.py:208-218`; `pre_screening_service.py:454-488`; `holistic.py:331-339` | Decide whether context changes should re-score; either coarsen/drop it from the key or mirror it into the re-run trigger. Measure the prod hit rate. |

### 3.3 Speed / latency

| # | Finding | Sev | Location | Fix |
|---|---|---|---|---|
| S1 | **Per-tick cap of 50 + 60-min beat = 50 candidates/hour/role.** A backlog of N takes ⌈N/50⌉ hours to even enqueue the cheap gate; tens of thousands = days. No fast lane for the ~$0.0002 gate. (Bounded by manual `batch_score_role`; no fairness harm.) | medium | `agent_tasks.py:582,585,660-663`; `celery_app.py:160` | Give the cheap pre-screen its own higher per-tick cap / faster sweep, decoupled from the expensive full-score cap. |
| S2 | **Manual batch pre-screen runs single-threaded in-process on the web server** — cohort wall-clock = sum of per-candidate round-trips (240s worst case each), competing with live requests. The cheap filter is delivered more slowly across a cohort than the expensive (fanned-out) score. | medium | `applications_routes.py:3765-3786,3871-3877` | Move to a Celery task with bounded per-candidate fan-out, like `batch_score_role`. |
| S3 | **3–4 serial committed DB sessions bracket one fast Haiku call** (cache get/set, usage_event, call_log). Pure fixed overhead on the path meant to be quickest. (The multi-session design is partly load-bearing for FK visibility.) | low | `runner_pre_screen.py:113,130,157,177`; `metered_anthropic_client.py:202-227,579-602` | Fold the hit-count bump into the read transaction; batch/queue metering writes off the hot path (preserving FK visibility). |

### 3.4 Robustness / path divergence

| # | Finding | Sev | Location | Fix |
|---|---|---|---|---|
| **R1** | **HIGH — Sub-agent pre-screen omits the must-have requirements arg.** It calls `run_pre_screen(cv, jd, ...)` with `requirements=None`, so the `<MUST_HAVE_REQUIREMENTS>` block is empty and the cache key's `must_haves=[]`. The prompt says "base the score on must-have requirements only" — the sub-agent gives the model none. The two paths score the same candidate differently and can't share cache; the sub-agent's score feeds the deterministic verdict that can auto-reject. (Bounded: a fast-path reuses the canonical score when present; divergence bites on fresh agent-loop pre-screens.) | **high** | `sub_agents/pre_screen.py:190-201` vs `pre_screening_service.py:234-256,299-306` | Build the same `RequirementInput` list and pass it — better, route the sub-agent through `execute_pre_screen_only` for one canonical implementation. |
| R2 | **Sub-agent resolves a different JD** (description/additional_requirements fallback + recruiter overlays appended) than the service (`job_spec_text` only), fragmenting the cache and producing a different score; a role with no `job_spec_text` is skipped by the service but scored by the sub-agent. | medium | `sub_agents/pre_screen.py:37-59,154,166` vs `pre_screening_service.py:224` | Pick one JD-resolution policy and apply it in both paths. |
| R3 | **Copy-paste detector hard-caps legit JD-quoting CVs** at the 0.05 threshold: a 20-word JD phrase in a 1469-char CV scores 0.091 (triggered); a standard skills list scores 0.94. Caps to 10, brands "fraud," skips full scoring. (Severity medium given gate-off default + one cited path effectively dead.) | medium | `fraud_detection.py:74-151,142-143` | Raise/tune the threshold; require multiple distinct runs; exclude short CVs and boilerplate; soften to a flag with the hard cap reserved for ≥0.30. |
| R4 | **Non-Latin tokenizer gap.** `_WORD_RE=[a-z0-9]+` → Arabic/Chinese/Cyrillic CVs tokenize to zero, silently skipping copy-paste detection. Relevant to the MENA market. (False-negative direction.) | medium | `fraud_detection.py:33,70-71,93-100,142` | Unicode-aware `\w+`/`\p{L}\p{N}`; denominator on token-char count; log when a CV is unevaluable. |
| R5 | **Holistic (org 2) applies none of the v3 integrity penalties.** A CV with future dates / impossible spans / unverified claims gets a clean Sonnet score for the flagship org. (Harm direction is leniency, not false-reject.) | medium | `holistic.py:299-394` vs `runner.py:380-387` | Port the bounded (≤15-pt) timeline + unverified-claim penalty into `holistic._to_output`. |
| R6 | **Transient pre-screen error hard-stops scoring for up to 6h** (false-reject-by-delay), with a stale docstring claiming the opposite. Deliberate trade against the 7,668-burned-call incident, but it converts an infra blip into a multi-hour gap. | medium | `runner_pre_screen.py:282-291`; `cv_score_orchestrator.py:590-616`; `pre_screening_service.py:451` | Distinguish transient (rate-limit/5xx/timeout) from deterministic errors; short bounded retry for transient; fix the docstring. |
| R7 | **Gate falls back to `pre_screen_score_100`** — the column a prior cv_match run can overwrite (the documented contamination bug `rescore_wrongly_filtered_prescreen` repairs). Latent residual hole in an already-mostly-fixed bug, scoped to a legacy evidence-shape tail. | medium | `cv_score_orchestrator.py:576-582` | Prefer `genuine_pre_screen_score_100`, or fall through to full scoring when no genuine this-run score is available. |
| R8 | **Constraint-bucket priority drift:** `constraint` is MUST_HAVE in pre-screen but STRONG_PREFERENCE in v3 (which never emits `Priority.CONSTRAINT`). Legacy v3 path only; lenient direction. | low | `pre_screening_service.py:241-249` vs `cv_score_orchestrator.py:527-538` | Share one criteria→RequirementInput helper; route `constraint` to `Priority.CONSTRAINT`. |

### 3.5 Fairness, bias & legal risk

This is the highest-stakes dimension for an AEDT and the area least covered by code today.

| # | Finding | Sev | Location | Fix |
|---|---|---|---|---|
| **F1** | **HIGH — No adverse-impact monitoring on the live gate / fraud cap / auto-reject.** The only EEOC 4/5ths audit gates the *shadow* threshold learner and runs on an empty holdout. The continuous bias monitor is a stub returning an empty report. There is zero standing protected-class measurement on the decision that actually rejects candidates — a direct EEOC Uniform-Guidelines + NYC LL144 gap. | **high** | `threshold_calibration/service.py:53-104`; `audit_examples.py:108-139`; `config/bias_audit_examples/` (README only) | Stand up a scheduled adverse-impact monitor on actual reject outcomes (selection rate by available proxy segment), persist + alert on 4/5ths breach; treat the gate as the AEDT-of-record. |
| F2 | **Deterministic 8-gram heuristic can auto-reject + brand "potential fraud" with adverse-impact + defamation risk** (recruiter-advised keyword mirroring, short CVs, certification names). (Medium: by default it routes to a HITL card, not silent disqualify — irreversible only when the role opts into auto_reject.) | medium | `fraud_detection.py:74-151,179-239`; `pre_screen_decision_emitter.py:93-94`; `cv_score_orchestrator.py:620-626` | Reclassify to a soft, recruiter-surfaced FLAG; reserve the hard cap for very-high overlap and route to HITL; neutral framing; tune against real CVs. |
| F3 | **Protected-characteristic safeguard exists only in the conversational agent prompt; the deterministic reject path bypasses it.** The gate reads raw CV + Workable answers + recruiter comments ("same weight as the CV") — free text carrying age/nationality/gender/visa signals — with no fairness guardrail (the holistic engine also dropped its demographic-non-inference rule). | medium | `agent_chat/system_prompt.py:146-150`; `pre_screen_decision_emitter.py:12-17`; `prompts_pre_screen.py:57` | Make the protected-characteristic constraint a code-level invariant on the deterministic path (proxy-detection/redaction pre-pass) + the F1 monitor. |
| F4 | **No candidate notice, explanation, or appeal for automated rejections.** Only internal Workable notes/events; no AEDT-used disclosure, job-relevant reason, or human-review route. EU AI Act high-risk + LL144 notice exposure. (Bounded: auto-disqualify is per-role opt-in; default is a HITL card.) | medium | `application_automation_service.py:404-450` (+ absence across services/decision-policy) | For auto-disqualify orgs: record AEDT use, the job-relevant reason in candidate-appropriate language, and a documented human-review/appeal route. |
| F5 | **Transient-error 6h hard-stop = availability-driven adverse effect** with no proactive alert under sustained outage / budget pause. (Low; recruiter-visible via `score_status`, self-healing.) | low | `cv_score_orchestrator.py:590-616` | Alert on cohorts stuck in `pre_screen_errored`; distinguish vendor errors; safe direction is KEEP the candidate. |
| F6 | **Unverified-claim penalty keys on LLM "familiarity,"** systematically under-recognising legitimate awards/journals from non-Western/smaller institutions (national-origin proxy). (Low: soft, fail-open, AND-gated, can't auto-reject.) | low | `prompts_pre_screen.py:59`; `fraud_detection.py:389-395,242-255` | Prefer corroboration-from-CV over model recognition; segment-monitor flag incidence; document the regional-familiarity bias. |

---

## 4. Integrity & fraud modules — proposed

**Unifying principle (from the platform's own rule and the law alike):** integrity signals become a **separate 0–100 axis** (100 = clean) that **never silently mutates the fit/role_fit score**, feed a **bounded soft penalty or a HITL flag**, and **never auto-disqualify on their own**. Cheap-deterministic-first; escalate to one cheap LLM call only on the ambiguous band; bias-monitor before any hard band ships. Everything below restores parity for the holistic org and removes the existing hard-cap auto-reject as the default.

### 4.1 Copy-paste / spec-mirroring & keyword-stuffing — **P2, effort L**

- **Gap.** Single whole-CV n-gram fraction → hard cap to 10 at ≥5%; verbatim-only and Latin-only; blind to hidden/white-font and metadata keyword stuffing; defamatory "fraud" framing with no human review.
- **Approach.** Replace the binary cap with a calibrated 0–100 integrity-risk signal: (A) section-aware Unicode-tokenized overlap requiring ≥2 distinct runs or one long run, weighting skills-list mirroring up and prose down, minus a boilerplate stoplist; (B) structural keyword-stuffing entropy (free); (C) raw-document hidden-text scan (font-size/render-mode-3/off-canvas + docx core-props) — flag-gated, borderline-band only; (D) fold one extra field into the *existing* Haiku call for the borderline band (≈$0 marginal). Soft penalty by default; hard cap reserved for ≥0.30 + multiple runs and even then routed to HITL.
- **Hooks.** `fraud_detection.py` (Unicode `_WORD_RE`, `detect_spec_mirroring`, `detect_keyword_stuffing`, `detect_hidden_text`, `compute_integrity_risk`); single wiring at `pre_screening_service.py:280-282`; `document_service.py` visitor (already receives font_size/tm, discards them); converge `sub_agents/pre_screen.py:218-247`; neutral label at `pre_screen_decision_emitter.py:93-94`.
- **Cost/latency.** Common path ~$0 / microseconds (in the Celery job). Borderline raw-bytes adds one object-store fetch (~50–200ms) + PDF re-parse. No new round-trips for the folded LLM field. No recruiter-perceived latency.
- **FP & fairness risk.** This is a net FP *reduction*; the Unicode fix removes an existing script/region disparate-impact. Keyword-stuffing entropy can flag honest skill-dense/ESL CVs → must stay soft + segment-monitored. Never auto-reject on its own.
- **Priority rationale.** High-value fairness fix but L effort and depends on the integrity-axis framing decision; sits behind the P0/P1 work.

### 4.2 AI-generated-CV / AI-usage — **P2, effort L — low-weight HITL context, never a gate**

- **Gap.** No AI-authorship signal — but the right answer is *not* to detect-and-reject. AI-assisted CVs are near-universal and benign.
- **Approach.** Deterministic-first context chip only. Tier 0 (free, at upload): PDF/DOCX metadata signatures (Producer/Creator strings, missing author, round timestamps). Tier 1 (free): stylometry (burstiness, type-token ratio, lexical tells, bullet uniformity). Tier 2 (LLM, ambiguous band only): one Haiku call for **register-consistency-across-sections** (far more robust and less ESL-biased than absolute AI-probability). **Weight ZERO on every automated decision** — renders as a "verify authorship in assessment/interview (context only)" chip.
- **Hooks.** New `ai_authorship_detection.py`; merge into `fraud_signals` in `execute_pre_screen_only` with no call to `apply_fraud_penalty`; metadata captured at `document_service.py` (new nullable `cv_authorship_metadata` column, since raw bytes only exist at upload).
- **Cost/latency.** Tier 0/1 ~$0; Tier 2 only the ~10–15% middle band, ~$0.0002–0.0015, in the async job.
- **FP & fairness risk — be honest: this technique is unreliable.** Research (Liang et al.) found a **61% false-positive rate on non-native English writers**; OpenAI killed its own detector (26% TP / 9% FP); detectors are cut ~88% by paraphrasing; 40+ universities abandoned Turnitin's detector. Taali's user base is heavily non-native English (UAE/MENA), where the bias is maximal, and EEOC holds the employer liable even for vendor tools. Hence: **never gate, never down-score, never auto-reject; weight 0; neutral framing with the unreliability caveat baked into the payload; tier-1 alone can never reach the "high" band without metadata corroboration; never read name/nationality.** Metadata (a verifiable fact about the file) is the only defensible layer, and even it ≠ "AI-written."
- **Priority rationale.** Genuinely useful only as low-stakes context; the real anti-fraud moat is the assessment stack, so this is P2.

### 4.3 Lying / claim-verification / credential-fabrication ("Integrity v2") — **P1, effort L**

- **Gap.** Timeline + integrity penalties are dormant on the holistic org; `claims_to_verify` has no holistic pass; the unverified-claim check relies on the model's prior alone and never cross-checks the suspect claim against verbatim CV evidence — even though native Anthropic **Citations** is already built (`candidate_search/grounded_evidence.py`).
- **Approach.** Three tiers. Tier 0 (free, both engines): existing timeline detection + NEW deterministic title/seniority-inflation (claimed senior title vs total tenure span) + credential-plausibility (degree-mill/self-conferred lexicons). Tier 1 (near-free): feed `claims_to_verify` into `compute_integrity_penalty`; extend the **holistic report call's** tool schema to emit `claims_to_verify` (zero extra calls). Tier 2 (LLM, escalation only): when a claim is genuinely uncertain, one **Citations** cross-check — a claim the candidate can't ground in their own CV becomes a high-precision, verbatim-cited flag; a grounded claim is cleared.
- **Hooks.** New `integrity_signals.py` reusing `fraud_detection` primitives + `verify_claims_grounded` over `extract_cv_evidence`; wire into `holistic._to_output` (closes the org-2 blind spot) and unify `runner.py:377-388`; HITL via `queue_integrity_review` surfacing the specific claim + missing evidence.
- **Cost/latency.** Tier 0/1 ~$0; Tier 2 only the ~5–10% escalated, ~$0.002–0.004, in the async job (the Citations helper already has a 20s timeout + degrade).
- **FP & fairness risk.** Penalty stays capped at 15 and never single-handedly rejects. Title-inflation flags *escalate*, never penalise alone. Citations grounds against the candidate's own documents (absence of self-evidence, not model disbelief). **Ship Tier 2 in SHADOW first** (persist, don't act) until a labelled set + bias monitor exist.
- **Priority rationale.** P1 because it restores a deliberately-designed control that is currently dead for the flagship org, and grounding is the *defensible* way to detect lying (check facts, not "AI-ness").

### 4.4 Identity / contact fraud & duplicate / CV-farm detection — **P2, effort L**

- **Gap.** Every signal today is single-application. No cross-application reasoning: same human re-applying under many emails, disposable domains, a CV template recycled across "candidates," dead portfolio links — all sail through the per-app gate.
- **Approach.** New `identity_fraud.py`, four org-scoped deterministic signals via indexed lookups: (1) duplicate-identity (distinct candidates sharing `phone_normalized`/email across different rows — a dedup *miss*, not normal multi-role applying); (2) disposable-email (static blocklist, dict lookup); (3) CV-farm near-duplication (MinHash/SimHash Jaccard over a per-application signature computed once at ingest); (4) link-liveness (syntactic v1; optional rate-limited async HEAD check). All informational/soft flags.
- **Hooks.** `pre_screening_service.py:280` merge into `fraud_signals['identity']`; new columns `cv_simhash`, `identity_signals`, `link_liveness_checked_at`; reuse the existing org-scoped dedup query shape.
- **Cost/latency.** ~$0 Anthropic; 2–3 indexed lookups in the session already open; signature precompute moved off the scoring path; liveness fully async.
- **FP & fairness risk.** Most legally sensitive: disposable-email/dead-link correlate with socioeconomic/regional status; name-token matching conflates common surnames. **Phone/email keys primary, name-only weak/informational; disposable + link signals informational-only (don't even nudge); flags → HITL never auto-reject; the F1 monitor must cover these before any org acts on them.**
- **Priority rationale.** High signal-per-dollar and addresses the AI-candidate/CV-farm trend (Gartner: 1-in-4 fake profiles by 2028), but net-new surface area → P2.

### 4.5 Unified candidate integrity score — **P1, effort L (the framework that ties 4.1–4.4 together)**

- **Gap.** Signals are fragmented, partly weaponised as hard auto-rejects, conflated with the fit score, dead on the holistic org, and unmonitored for adverse impact. "Agent warns, never blocks" is honoured only in prompt text and bypassed by the deterministic path.
- **Approach.** One integrity score on a **separate 0–100 axis** that never silently mutates fit. Aggregate existing deterministic + free LLM-tagged signals into a weighted, capped composite (no single noisy signal can sink a candidate). Map to a 4-way route: `pass | nudge | flag-for-HITL-with-reasons | rare hard-fraud auto-filter`, where only an extreme **multi-corroborated** band auto-filters, gated behind org policy and HITL by default. Surface as a recruiter-facing "Integrity ✓/⚠ verify" chip with verbatim reasons. Feed routing outcomes into the existing EEOC 4/5ths machinery.
- **Hooks.** New `integrity_score.py`; compose at `execute_pre_screen_only`; replace the unconditional `persist_fraud_filtered_prescreen` short-circuit with a route decision; orchestrator reads `integrity.route` instead of `fraud_capped`; holistic parity in `_to_output`; emitter reads structured reasons instead of the hard-coded "fraud" string.
- **Cost/latency.** Common path zero added cost; escalation band one Haiku call; also *reduces* cost by skipping the holistic 5000-token report on clear hard-fraud.
- **FP & fairness risk.** This is specifically a false-positive *reduction* (multi-signal requirement before any auto-filter) and the place to make "agent warns, never blocks" a code invariant on the deterministic path. Prereq: the Unicode tokenizer fix; keep hard auto-filter off by default until a labelled holdout + bias audit exist.
- **Priority rationale.** P1 because it is the structural fix that makes every other module safe and removes the live hard-cap auto-reject default.

**On AI-detection specifically — the honest bottom line:** it is the one technique here that is both biased (61% FP on non-native writers) *and* ineffective (~88% defeated by paraphrasing). Treat it as zero-weight context at most. Taali's real fraud-resistance is its sandboxed, graded **assessments** — competence is measured by what candidates *do*, which sidesteps the entire detection minefield. Lean on that, not prose forensics.

---

## 5. External grounding

**AI-text detection is unreliable and biased — do not gate on it.**
- Liang et al. (Patterns 2023): 7 detectors averaged a **61.22% false-positive rate** on non-native TOEFL essays; **18/91 (19.78%)** unanimously misclassified; **97.80%** flagged by at least one — yet near-perfect on native 8th-grade essays. The mechanism (low perplexity) *is* the ESL signal. ([arXiv 2304.02819](https://arxiv.org/abs/2304.02819))
- OpenAI built then **killed** its own detector (26% TP / 9% FP) on 20 Jul 2023. ([TechCrunch](https://techcrunch.com/2023/07/25/openai-scuttles-ai-written-text-detector-over-low-rate-of-accuracy/))
- Adversarial paraphrasing cuts detection **~88%** on average. ([arXiv 2506.07001](https://arxiv.org/abs/2506.07001))
- Vendor "99%" claims vs measured **5–8%** (GPTZero) / **4.79–5.7%** (Originality.ai) real-world FP. Vanderbilt, Waterloo, MIT, Yale, Berkeley + 40 others **disabled** AI detection. ([Vanderbilt](https://www.vanderbilt.edu/brightspace/2023/08/16/guidance-on-ai-detection-and-why-were-disabling-turnitins-ai-detector/))
- Watermarking (SynthID-Text, Nature 2024) needs generator cooperation, is absent on third-party CVs, and degrades under paraphrasing — **not usable for inbound CVs.**

**CV fraud is real but caught by verification/assessment, not prose forensics.**
- Self-report 24–44% (recent/material; ResumeBuilder 2025) vs verified discrepancy rates: HireRight 2025 — **>75%** of businesses found ≥1 discrepancy/year, ~**20%** discrepancy rate ("discrepancy" ≠ fraud — many are honest errors). Most-faked categories: **experience/responsibilities, skills (~34%), dates, titles, degrees.** Most lies surface at **interview (~38% of catches)**, not the ATS scan. ([HireRight](https://www.hireright.com/company/newsroom/identity-fraud-and-candidate-discrepancies-remain-key-concerns-for-employers))
- Emerging threat: Gartner projects **1 in 4 candidate profiles fake by 2028**; ~17% of hiring managers report deepfake interview candidates — shifting the problem to "is this person real" (identity/liveness), not "is this claim exaggerated." ([CNBC](https://www.cnbc.com/2025/07/11/how-deepfake-ai-job-applicants-are-stealing-remote-work.html))
- Cheapest high-yield signals: CV↔LinkedIn reconciliation, internal timeline consistency, verbatim-JD detection, **skills assessment** (directly verifies the most-faked category). Skills are best countered with a proctored work-sample — Taali's structural advantage.

**Screening law & adverse impact — the gate is the legally sensitive act.**
- EEOC/Uniform Guidelines: an automated screen with disparate impact violates Title VII unless job-related + business-necessity, with no less-discriminatory alternative. The **four-fifths rule is a rough screen, not a safe harbor** — with large applicant pools, ratios above 0.80 can still be statistically significant. ([Mayer Brown](https://www.mayerbrown.com/en/insights/publications/2023/07/eeoc-issues-title-vii-guidance-on-employer-use-of-ai-other-algorithmic-decisionmaking-tools))
- **Mobley v. Workday**: the AI *vendor* can be directly liable as the employer's "agent" for software that "recommends some candidates and rejects others"; May 2025 conditional ADEA collective certification, ~**1.1 billion** applications in scope; Mar 2026 ruling held ADEA covers applicants. Directly relevant to the public-API / Workable-provider productization. ([Seyfarth](https://www.seyfarth.com/news-insights/mobley-v-workday-court-holds-ai-service-providers-could-be-directly-liable-for-employment-discrimination-under-agent-theory.html))
- **NYC LL144**: an AEDT that "substantially assists/replaces" a hiring decision needs an annual independent bias audit (selection rate + impact ratio by sex, race/ethnicity, and intersections), a public summary, and ≥10-business-day candidate notice with an alternative-process route. ([DCWP rule](https://rules.cityofnewyork.us/rule/automated-employment-decision-tools-2/))
- **EU AI Act**: recruitment AI is explicitly **high-risk** (Annex III(4)(a)); a human final approver does not remove the classification; extraterritorial whenever output affects EU-located people; obligations include logging/traceability, human oversight (Art. 14), and candidate explanation. High-risk obligations provisionally delayed to **Dec 2, 2027** (Digital Omnibus, pending adoption). ([Annex III](https://artificialintelligenceact.eu/annex/3/))
- US state divergence + federal pullback: Illinois (Jan 1 2026), California FEHA (Oct 1 2025), Colorado repealed-and-replaced (Jan 1 2027). EEOC removed its AI guidance in Jan 2025 and EO 14281 directs agencies away from disparate-impact — **but the statutes are unchanged and enforceable by private plaintiffs, class actions, and state AGs; litigation is expected to *increase*.** ([Cooley](https://www.cooley.com/news/insight/2025/2025-02-21-gone-but-not-forgotten-federal-laws-still-apply-despite-guidance-disappearance-act))

**Cheap-LLM cascade — Taali already has the topology; the missing pieces are calibration + a borderline band.**
- FrugalGPT: a learned cheap→expensive cascade matches GPT-4 quality at up to **~98% lower cost.** ([arXiv 2305.05176](https://arxiv.org/abs/2305.05176))
- Cascaded Human-AI (arXiv 2506.11887) is a near-exact map of Taali's base→large→human topology with two gates, tuned against a single joint risk `R = (1−E[correct]) + λ_c·E[cost] + λ_a·P(abstain)`. **Bayesian calibration on ~100 samples** makes thresholds stable; without it they're unstable. ([arXiv 2506.11887](https://arxiv.org/html/2506.11887v3))
- Raw LLM scores are **badly calibrated and overconfident** (ECE >0.377; RLHF over-estimates) — the dominant error direction is wrongly *accepting* (auto-rejecting) cases it should have escalated. So **bias the band conservatively toward escalation until calibration is proven.** ([arXiv 2305.14975](https://arxiv.org/abs/2305.14975))
- Don't escalate on verbalized self-confidence alone — prefer a trained quality estimator, surrogate logprob, or self-consistency (3 cheap samples ≪ 1 Sonnet call).
- Resume-screening accuracy: off-the-shelf LLMs hit ROC AUC 0.74–0.77 vs a tuned scorer's 0.85 — the **+8pt gap is precisely at the boundary**, the strongest argument to escalate the borderline band to the holistic engine rather than let the cheap model decide it. And **every off-the-shelf LLM failed the 4/5ths rule** (ratios 0.60–0.77) while the domain model hit 0.91–0.96 — calibration ≠ fairness. ([Eightfold, arXiv 2507.02087](https://arxiv.org/html/2507.02087v1))

---

## 6. Recommended roadmap

The gate has almost no ground truth today, so the first job is *measurement*, done cheaply and in shadow, before turning anything on.

### P0 — Stop the live false-reject and make the costliest error measurable (days; quick wins)
1. **Fix A7** — make `_LeanScore.overall` required (or add a semantic validator) so a degraded holistic emission returns FAILED, not a silent 0 auto-reject. This is the only confirmed *live* silent false-reject. (`holistic.py:153`)
2. **Fix R1** — route the sub-agent through `execute_pre_screen_only` (or pass the must-have list) so both paths share one prompt, cache key, and score. Highest-severity divergence feeding the deterministic verdict. (`sub_agents/pre_screen.py:190`)
3. **Wire a consumer for `prescreen_calibration_samples`** and extend the weekly shadow-sampler to **full-score a sample of gate-*filtered* candidates** — this is the only way to observe the false-reject rate at 30/25/20. (`prescreen_calibration.py`)
4. **Stand up the adverse-impact monitor (F1)** on actual reject outcomes (selection rate by available proxy segment), persisted + dated like score-provenance, alert on 4/5ths breach. The continuous monitor is a stub; the holdout is empty. This is a hard prerequisite for any hard-band rollout.
5. **Docstring/threshold-band hygiene** (A2, A8): parameterize the divergence bands on `PRE_SCREEN_THRESHOLD`; fix the stale runner/prompt docstrings.

**What to measure:** false-reject rate from the shadow-scored filtered sample; gate-vs-holistic agreement (κ, Brier, ECE — the metrics module already exists in `evals/metrics.py`, it just lacks data); selection-rate impact ratios by segment.

### P1 — Make the cascade correct, fair, and the integrity layer real (1–2 sprints; the core bets)
6. **Calibrate the cheap score and define a borderline band.** Fit a logistic/isotonic map from raw gate score → empirical advance-rate on the collected samples; auto-decide only outside a tuned band, escalate the band to the holistic engine, abstain-early to HITL on un-resolvable cases. Bias conservative toward escalation (overconfidence is the dominant failure). This is what makes the gate both cheaper *and* more accurate.
7. **Run the small-vs-large agreement study** Taali is uniquely positioned to do (it has both engines + recruiter outcomes): bucket by calibrated probability, find the disagreement zone, set the band to exactly that zone.
8. **Build the unified integrity axis (4.5)** + **port integrity parity into holistic (R5/4.3)** + **reframe copy-paste to a soft HITL flag (R3/F2)** + **Unicode tokenizer (R4)**. Make "agent warns, never blocks" a code invariant on the deterministic path. Ship Tier-2 grounding in shadow.
9. **Holistic prompt caching (C2)** — the single biggest cost lever on the most expensive engine. Hoist the shared role block into a leading `cache_control` block.

### P2 — Throughput, cost tail, and the net-new integrity modules (larger bets)
10. **Turn the gate on deliberately** (C1) once the band is calibrated and the monitor is live; document the flag.
11. **Decouple the cheap gate's per-tick cap (S1)** and **fan out manual batch pre-screen via Celery (S2)**.
12. **Holistic report skip on clear rejects (C3)** + **route bulk/backfill through the Batches runner (C4)**.
13. **Identity/CV-farm module (4.4)** and **AI-authorship context chip (4.2, zero-weight)** — both behind the bias monitor, flags-only.

### P3 — Compliance posture & per-org policy layer
14. **AEDT compliance scaffolding** (F3/F4): code-level protected-characteristic invariant on the deterministic path; candidate AEDT-disclosure + job-relevant reason + appeal route for auto-disqualify orgs; per-org policy flags for NYC (bias audit + notice) and EU (human oversight + explanation). Extend score-provenance into reject-provenance (inputs, engine_version, threshold, verdict, reason, human override) — this is the Mobley defense record and the EU logging duty in one.
15. **Document the deliberate decision not to use AI-text detection as a screening criterion**, citing the ESL FP rate and EEOC exposure — a compliance/trust asset for the public-API positioning.

---

## 7. Open questions for Sam

1. **The load-bearing unknown: what are the live Railway values of `ENABLE_PRE_SCREEN_GATE` and `HOLISTIC_SCORING_ORG_IDS`?** Everything about "what runs in prod" — and the cost and false-reject blast radius — hinges on whether the gate is on for org 2. The repo default is off; this cannot be verified from code.
2. **Is the holistic org skipping CV-integrity penalties intentional or an oversight from the v2.1.0 cutover?** If intentional, what replaces timeline/claim integrity for the flagship org? (The fix is low-risk — all penalties are bounded.)
3. **Fairness reframe sign-off:** per "agent warns, never blocks," should the copy-paste/integrity signal become a soft flag + HITL with the hard cap removed as a default? This changes live reject behaviour and is your call.
4. **Should any org ever run silent auto-disqualify, or should every reject route through a human?** This is the single biggest legal-posture decision (Mobley, EU AI Act, LL144) and determines how much compliance scaffolding P3 needs.
5. **Where do you want to invest the anti-fraud effort — claim-verification/grounding + assessments (defensible, your moat) vs. detection heuristics (cheap but legally fraught)?** The research is unambiguous that AI-detection is biased and ineffective; do you want it even as zero-weight context, or omitted entirely?
6. **Ground truth ownership:** who adds the first N labelled cases (ideally including known-good candidates the gate would see) and against which roles? Without labels, every accuracy claim is unmeasurable.
7. **Identity/liveness for assessments:** with deepfake/CV-farm candidates rising, do you want an identity-verification seam before it becomes a customer ask — and does that put Taali into FCRA/consumer-report territory if customers act on it?
8. **Per-org compliance flags vs. a global policy:** do you want NYC-tied orgs to enforce the bias-audit + 10-day-notice + alternative-process flow and EU-affecting orgs to enforce human-oversight + explanation, as org-scoped settings?
