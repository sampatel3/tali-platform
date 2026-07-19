# Taali Pre-Screen Scoring & Integrity Stage — Engineering Deep-Dive

*For Sam. Backend is Python/FastAPI/SQLAlchemy/Celery. All paths are under `backend/`.*

---

## 0. Production-grounded addendum (verified 2026-06-14)

The report body below was written from the repo, which carries conservative defaults. I read the **live Railway config** to ground it. The truths that differ from the defaults:

| Setting | Repo default | **Live prod** | Note |
|---|---|---|---|
| `ENABLE_PRE_SCREEN_GATE` | `False` (now `True` on main, PR #618) | **`true`** (API + scoring worker) | The gate is ON. |
| `PRE_SCREEN_THRESHOLD` | `30` | **Observed as `50` on the API and unset (→30) on both workers before the fix** | Coordinated rollout now propagates the web policy to both workers. |
| `HOLISTIC_SCORING_ENABLED` / `_ORG_IDS` | now `True` / `"*"` on main (PR #618) | **`true` / `*`** (scoring worker) | Holistic Sonnet is the **platform-wide default — LIVE for ALL orgs**, so the A7 silent-zero bug affected every org's new scores. |
| `CLAUDE_MODEL` / `CLAUDE_SCORING_MODEL` | haiku / "" | `claude-sonnet-4-5` | Agent/chat on Sonnet; the pre-screen gate still pins Haiku (`FAST_MODEL`) regardless. |

**Threshold drift found in prod, now deployment-guarded.** The autonomous scoring funnel previously ran in **`taali-worker-scoring`** with `PRE_SCREEN_THRESHOLD` unset (code default 30) while the API had 50. The coordinated production preparation now reads the web service's validated `PRE_SCREEN_THRESHOLD` and `ENABLE_PRE_SCREEN_GATE` policy and pins the same values on both workers before deployment. Worker heartbeats also publish this fingerprint and readiness fails with `config_mismatch` if a service drifts later. Existing live services still need the coordinated rollout to receive the fix.

### What shipped in this PR (provably never-worse)
- **A7 fix** — `_LeanScore.overall` made **required** (`holistic.py`). Previously a degraded-but-schema-valid Sonnet tool emission that omitted `overall` validated as `overall=0` → `cv_match_score=0` with status OK = a **silent 0-score auto-reject of a real candidate — live across ALL orgs** (holistic is the platform default, PR #618). Now an absent field raises in the structured layer's `model_validate` → `ValidationFailure` → retry-with-feedback → only-if-still-missing `FAILED` (→ `cv_match_score=None`, retried later — never a 0 auto-reject). A genuine model-emitted `overall=0` (real clear-misfit) still passes. Verified: the FAILED branch (`cv_score_orchestrator.py:743`) sets `cv_match_score=None`, never persisting the 0. Regression test added; holistic/orchestrator/runner/qa/gateway/arch suites green.
- **Prescreen consistency bundle (A1/A3/A4/A6, R1/R2/R7/R8)** — the filtered-candidate shadow sample now feeds both the gate calibrator and divergence monitor; Stage 1, both scoring sub-agents, and the full scorer share canonical JD/criteria conversion; constraint remains `Priority.CONSTRAINT`; authored recruiter intent and teach examples are preserved; decisions/snapshots read only the durable penalized genuine score; legacy rows without it fail open to full scoring; and pre-screen reject cards enforce the Stage-1 cut rather than the downstream role send bar.

### Current-tree residual status (verified 2026-07-15)

| Findings | Status in the current tree |
|---|---|
| C2 | **Resolved.** Both holistic calls use the same one-hour ephemeral prompt-cache layout for stable role context; focused tests inspect both requests. |
| C3 | **Intentional.** The holistic report is retained for every completed holistic score because it is the durable recruiter explanation/audit artifact, including for clear rejects. Removing it would reduce product usefulness rather than deliver an equivalent optimization. |
| C4 | **Superseded.** The unused `runner_batch.py` experiment was removed in cleanup #826 (`4f7a3e05`). Recruiter scoring now uses durable per-application Celery fan-out; Anthropic Message Batches remain enabled for latency-tolerant CV parsing, where the discount does not weaken interactive progress or recovery semantics. |
| S1 | **Resolved.** New applications enqueue event-driven, activation bootstraps up to 500, and a bounded five-minute deterministic sweep drains standing/imported backlogs through the same credit, budget, duplicate-job, and retry guards without buying an extra agent-reasoning cycle. |
| S2 | **Resolved.** Manual pre-screen persists a run and per-application items before bounded Celery fan-out. DB dispatch leases/token-CAS recover publisher/broker loss; the worker commits an `attempting` marker before paid I/O so duplicates do not pay, and a stale response/commit crash window is surfaced as terminal `ambiguous` rather than silently repaid. A one-minute sweep, late acknowledgements, and DB progress cover deploy failure windows. The unified Process cascade also runs in Celery, with Redis-authoritative progress instead of a web-process daemon thread. |
| R3 / F2 | **Resolved safely.** Copy-paste detection is always recorded but defaults to a neutral recruiter review flag. The former hard cap remains explicit opt-in via `FRAUD_COPY_PASTE_ACTION=cap`. |
| R4 | **Resolved.** Tokenization is Unicode letter/number aware and covered by a non-Latin regression. |
| R5 | **Resolved.** The holistic engine computes and persists the same bounded timeline/unverified-claim integrity layer; application is controlled by `HOLISTIC_INTEGRITY_PENALTY_ENABLED`, now enabled by default and cache-keyed. |
| F1 | **Implemented, opt-in.** A daily rolling monitor joins only segregated voluntary EEO self-ID to actual gate, fraud-cap, and automated-reject outcomes, suppresses small cells, persists aggregates only, logs 4/5ths violations, and exposes the latest aggregate to owners/the continuous-bias capability. It is an honest no-op until explicitly enabled and remains `insufficient_data` until enough voluntary data exists. |

### Deliberately retained or still policy-gated
- **C3 holistic report** remains because it is a user-visible explanation and audit artifact, not redundant output.
- **Unified integrity-axis and net-new detection modules** remain P1/P2 product-policy work. New heuristics should stay shadow/flag-only until evidence and adverse-impact volume justify stronger action.

---

## 1. Executive summary

- **What the gate is.** Stage 1 is a cheap, permissive Haiku filter (`runner_pre_screen.py`, prompt `cv_pre_screen_v2.3`) inside the asynchronous CV-scoring job. Deterministic CV↔JD overlap is measured first and always persisted, but now defaults to a neutral review flag; only explicit `FRAUD_COPY_PASTE_ACTION=cap` restores the legacy hard short-circuit. Survivors flow to the platform-default two-call Sonnet holistic scorer, with Haiku v18 retained as the flag-controlled fallback.

- **Scoring parity is restored.** Both full-score engines now apply the bounded timeline/unverified-claim integrity layer, while every service/sub-agent path resolves the same JD, requirements, recruiter intent, and teach examples. The holistic engine remains a model-authored overall score rather than the Haiku weighted aggregate, but the surrounding inputs, integrity policy, failure semantics, and provenance are aligned.

- **How good it is today — accuracy.** Real-world labelled volume is still the limiting factor, but false rejects are no longer structurally invisible. A bounded weekly sampler full-scores actual filtered candidates in shadow; the divergence report and gate calibrator consume those rows alongside production survivors. Missing holistic `overall` is a retryable failure rather than a silent zero, and focused regressions cover threshold provenance, canonical inputs, Unicode overlap, and integrity wiring.

- **How good it is today — cost.** All three scoring paths use prompt caching, including the stable per-role system blocks on both Sonnet holistic calls. The holistic report still runs for clear rejects by design because it is the recruiter explanation/audit artifact. The repo gate default remains conservative/off while production policy is deployment-coordinated. The abandoned scoring Batches experiment was removed; Message Batches are used where their asynchronous latency is appropriate (CV parsing), while recruiter scoring uses recoverable per-application fan-out.

- **How good it is today — speed and durability.** New applications enqueue event-driven, role activation has a 500-candidate bootstrap, and a separate five-minute bounded sweep drains residual backlog without another paid agent cycle. Manual pre-screen is durable per-application Celery fan-out with leased dispatch recovery and DB progress. The unified Process cascade also runs off the web server and exposes Redis-authoritative state.

- **The biggest remaining problems.** (1) **Evidence volume and operational enablement** — the adverse-impact monitor is opt-in and must honestly report insufficient data until enough voluntary EEO observations exist. (2) **Input-window divergence (A5)** — the cheap gate and holistic scorer still inspect different CV/JD windows. (3) **Candidate compliance UX (F3/F4)** — protected-trait handling, notice, explanation, and appeal policy remain broader product/legal work. (4) **R6/S3/C5** remain bounded retry/session/cache-invalidation optimization opportunities.

- **Headline recommendations.** Treat gate and automated-reject outcomes as the AEDT-of-record, enable the aggregate monitor where voluntary EEO collection and governance are ready, and collect enough shadow/recruiter outcomes to validate the calibrated band. Preserve the current safety direction: copy-paste flag-only by default, integrity bounded and separately explained, and no AI-authorship detector in an automated decision. Align the remaining input windows and build candidate notice/appeal before expanding silent automation.

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
`execute_pre_screen_only` runs the deterministic copy-paste detector first. A hit is always persisted; the default `FRAUD_COPY_PASTE_ACTION=flag` continues to the LLM with no score mutation, while explicit legacy mode `cap` applies the configured cap and returns without buying the call. `run_pre_screen` then emits `{score 0-100, reason, unverified_extraordinary_claim}`. Bounded policy penalties apply, and the run persists the raw calibration value plus `genuine_pre_screen_score_100`, the authoritative decision/display score.

**Gate decision:** the orchestrator reads only the durable genuine pre-screen value and the threshold stamped for that run. Missing genuine provenance fails open to full scoring; it never falls back to the legacy display/raw columns. A score below the enforced gate writes the filtered provenance, leaves `cv_match_score=None`, and returns without full scoring. A pre-screen error is retryable and remains visible rather than manufacturing a reject score.

**Survivors / gate-off:** `run_holistic_match` is the current all-org default; `run_cv_match` remains the controlled fallback. Both produce the persisted role-fit value and bounded integrity provenance. A post-commit task evaluates filtered candidates for either the explicitly enabled Workable automation path or a Decision-Hub human-review card.

| | Haiku v18 fallback | Holistic default |
|---|---|---|
| Pre-screen gate engine | Haiku `cv_pre_screen_v2.3` | Haiku (same) |
| Full-score engine | `run_cv_match` Haiku v18 | `run_holistic_match` Sonnet |
| role_fit derivation | 0.40·cv_fit + 0.60·graded-req − integrity penalty | `overall` → `role_fit` directly (no aggregation) |
| Timeline/unverified-claim integrity penalty | Applied | Applied (default-enabled, flag-controlled) |
| Prompt caching | Yes | Yes on both calls' stable role blocks |
| Score-call count | 3 Haiku | 2–3 Sonnet |

### 2.2 Engines and score scales

- **Pre-screen (cheap floor, permissive):** instructed to score ≥60, "default 70 when uncertain," only <30 for obvious misfits, **must-haves only.** Its purpose is a cheap floor, not a predictor.
- **Holistic (decisive, full-range):** told to "use the FULL range, do NOT cluster" — 75–100 strong / 35–54 weak / 0–30 clear misfit.
- **The two 0-100 scales are not calibrated to each other** and are not directly comparable. They are *meant* to agree only on pass/no-pass intent.

The runner's legacy yes/no field is explanatory only. The enforced Stage-1 threshold controls filtering and the pre-screen card; `role.score_threshold` remains a separate downstream full-score/send policy. Recruiter labels are display bands, not hidden decision thresholds.

### 2.3 Fraud/integrity signals that exist today

- **CV↔JD copy-paste** (deterministic n-gram overlap): a hit is recorded and neutrally review-flagged by default. The old cap is explicit opt-in only.
- **Timeline inconsistencies** (`fraud_detection.py:330`): future dates, end-before-start, impossible spans, >2 concurrent current roles. Bounded soft penalty.
- **Unverified extraordinary claim** (`fraud_detection.py:389-395`): penalises only when uncorroborated AND model-unfamiliar. Soft −5 nudge.
- **Integrity penalty** (`compute_integrity_penalty`, capped at 15): wired to both full-score engines and never single-handedly rejects.
- **Tokenizer is Unicode-aware** (`[^\W_]+` with casefold), so Arabic/Cyrillic and other letter/number scripts are evaluated instead of silently skipped.

### 2.4 Threshold & calibration

The global Stage-1 filter and downstream per-role send threshold are separate policies. A weekly bounded shadow-sampler (`prescreen_calibration.py`) full-scores actual gate-filtered candidates into `prescreen_calibration_samples`; `prescreen_gate_calibration.py` consumes both those reject-inference pairs and production survivor pairs, learns the highest 20–45 cutoff within a 1% false-reject budget, and exposes it in shadow unless `PRE_SCREEN_DYNAMIC_GATE_ENFORCE` is explicitly enabled. The divergence monitor reads the same shadow-reject rows, so its false-negative count covers the otherwise-unobservable filtered region. The separate nightly downstream threshold learner remains shadow/manual-activation and bias-gated.

### 2.5 Cost & latency per candidate

- **Pre-screen:** ~$0.0015 warm (cache-read), ~$0.0046 cold (cache-write), $0 on DB-cache hit or deterministic fraud short-circuit. Metered `Feature.PRESCREEN` (1.0× markup; `Feature.SCORE` is 3.0×). Sub-second to ~2s, inside the Celery job (off the request path). For sparse roles (1–2 candidates/hour) the 1h cache TTL can cost *more* than no caching.
- **Full score:** Haiku v18 is roughly an order of magnitude larger than pre-screen; Sonnet holistic is two calls (score + recruiter report) plus a cached derivation call. Both Sonnet calls cache stable role context; the report remains intentionally unconditional.
- **Reconciliation split:** pre-screen ~6% of spend, score ~43% — consistent with the gate being driven by recruiter batch actions, not the always-on funnel.

---

## 3. Evaluation — confirmed findings by dimension

### 3.1 Accuracy & correctness — *lead: the asymmetric false-reject risk*

For an asymmetric-cost gate, a false reject is the catastrophic error — it terminates a candidate with no second look. The standing instrumentation is structurally blind to exactly this class, and one live bug produces it silently.

| # | Finding | Sev | Location | Fix |
|---|---|---|---|---|
| A1 | **Fixed.** The weekly sampler shadow-scores genuine gate-filtered candidates without surfacing the score, and `pre_screen_gate_divergence_report` includes those `prescreen_calibration_samples` alongside production survivors (with separate pair counts and de-duplication). Autonomous false negatives are now observable. | resolved | `prescreen_calibration.py`; `pre_screen_decision_emitter.py` | Covered by a regression where a filtered candidate's shadow full score clears the send bar. |
| A2 | **Fixed.** The divergence report now reads the enforced gate threshold stamped on each candidate and compares the authoritative full score with the current org-wide send bar. Legacy rows fall back to `PRE_SCREEN_THRESHOLD`; the report exposes that fallback and its per-org send bars. | resolved | `pre_screen_decision_emitter.py` | Covered by a regression whose enforced threshold differs from the old 30/50 constants. |
| A3 | **Fixed (shadow-first).** `prescreen_gate_calibration.py` consumes survivor + shadow-reject pairs and recommends the highest bounded cut that stays within a 1% false-reject budget; a scheduled weekly consumer runs after sampling. Insufficient data holds at the conservative floor and live enforcement remains explicit. | resolved | `prescreen_gate_calibration.py`; `calibration_tasks.py`; `celery_app.py` | Unit coverage exercises safe-cut selection, low-positive fallback, caching, sampling, and schedule wiring. |
| A4 | **Fixed.** The runner's legacy yes/no field is explanatory only. Stage-1 labels and pre-screen reject cards use the stamped/enforced gate cutoff; the role score threshold is retained separately for downstream full-score/send policy. A 35 scorer passes a 30 gate even when the role send bar is 50. | resolved | `prescreen_gate_calibration.py`; `pre_screening_service.py`; `decision_policy/auto_reject.py` | Regression covers a 35/30/50 candidate and asserts no pre-screen reject. |
| A5 | **Gate scores untruncated CV/JD; holistic truncates to 14k/8k.** The two engines judge different input windows; the genuine false-reject path is decisive must-have evidence past char 14000 the holistic engine never sees. | medium | `runner_pre_screen.py:251-252` vs `holistic.py:89-91,370,382` | Align the gate's input window to the holistic limits, or remove the holistic truncation. |
| A6 | **Fixed.** `genuine_pre_screen_score_100` is the single authoritative decision/display value and includes applied bounded penalties. Raw `llm_score_100` remains calibration-only. The gate, snapshot, sub-agent fast path, and card all read the genuine column; full-score refresh no longer overwrites it. | resolved | `cv_score_orchestrator.py`; `pre_screening_snapshot.py`; `sub_agents/pre_screen.py` | Regressions separate a genuine 82 pre-screen from a full score of 10 and reject contaminated legacy fallback. |
| A7 | **Fixed.** Holistic `overall` is required. A partial tool emission enters structured retry/failure handling and never persists a fabricated zero; a genuine model-emitted zero remains valid. | resolved | `holistic.py`; structured validation; orchestrator failure branch | Covered by partial-emission and genuine-zero regressions. |
| A8 | **Fixed.** Runner and prompt documentation now match the retryable hard-stop behavior and the default threshold of 30. | resolved | `runner_pre_screen.py`; `prompts_pre_screen.py` | Documentation is aligned with the executable policy. |

The original live silent-zero path (A7) is closed; missing required output now fails safely and retries.

### 3.2 Cost-efficiency

| # | Finding | Sev | Location | Fix |
|---|---|---|---|---|
| C1 | **Intentional policy default.** The repo gate default is off; coordinated deployment can enable it only with the same fingerprint on API/scoring workers. This trades cost for conservative rollout rather than silently reducing capability. | policy | `config.py`; deployment preparation; worker heartbeat | Enable per governed rollout after calibration/monitor readiness. |
| C2 | **Fixed.** Stable role/JD/requirements context is a one-hour ephemeral cached system block on both holistic score and report calls; only candidate-specific content stays uncached. | resolved | `holistic._cached_system`; both call sites | Request-shape regressions inspect both calls. |
| C3 | **Intentional.** The report call remains unconditional because it supplies recruiter-facing evidence and a durable explanation/audit artifact even for clear rejects. Skipping it is not an equivalent optimization. | retained | `holistic.py` report call | Revisit only if an equally useful deterministic/cheaper artifact is designed and measured. |
| C4 | **Superseded.** The unused scoring Batches runner was deleted in cleanup #826 (`4f7a3e05`). Durable per-app Celery fan-out now serves recruiter scoring; Message Batches remain for latency-tolerant CV parsing. | superseded | scoring tasks; `cv_parsing/batch.py` | No resurrection of dead parallel scoring architecture; add a new batch mode only for a proven latency-tolerant workload. |
| C5 | **Cache key includes `workable_context` but the re-run trigger does not.** Activity-log churn busts the holistic cache (a bust = two full Sonnet calls) while the gate's staleness check never reacts to context changes. | low | `cache.py:51`; `runner_pre_screen.py:208-218`; `pre_screening_service.py:454-488`; `holistic.py:331-339` | Decide whether context changes should re-score; either coarsen/drop it from the key or mirror it into the re-run trigger. Measure the prod hit rate. |

### 3.3 Speed / latency

| # | Finding | Sev | Location | Fix |
|---|---|---|---|---|
| S1 | **Fixed.** Event-driven enqueue and the 500-candidate activation bootstrap handle normal arrivals; a separate bounded five-minute sweep drains old/imported backlog through the existing admission guards without running the hourly paid reasoning cycle. | resolved | `agent_tasks.agent_scoring_backlog_sweep`; Beat schedule | Focused regression proves the independent bounded drain. |
| S2 | **Fixed with conservative recovery.** Manual pre-screen materializes durable items, leases bounded dispatches with SKIP LOCKED/token-CAS, and fans per application through Celery. The one-minute sweep re-publishes queued expired leases, but stale paid-call attempts become visible `ambiguous` errors and are never automatically repaid. Progress is DB-derived. Unified Process also runs in Celery and reads Redis state first. | resolved | `prescreen_tasks.py`; `applications_routes.py`; migration 175 | Tests cover broker failure/ambiguity, multi-sweep exclusion, response-then-worker-death, duplicate delivery, route dispatch, and Redis-authoritative status. |
| S3 | **3–4 serial committed DB sessions bracket one fast Haiku call** (cache get/set, usage_event, call_log). Pure fixed overhead on the path meant to be quickest. (The multi-session design is partly load-bearing for FK visibility.) | low | `runner_pre_screen.py:113,130,157,177`; `metered_anthropic_client.py:202-227,579-602` | Fold the hit-count bump into the read transaction; batch/queue metering writes off the hot path (preserving FK visibility). |

### 3.4 Robustness / path divergence

| # | Finding | Sev | Location | Fix |
|---|---|---|---|---|
| **R1** | **Fixed.** Pre-screen service/sub-agent and full scorer/sub-agent use `build_scoring_requirements`; every runner receives the same active structured requirements. | resolved | `role_requirement_service.py`; `sub_agents/pre_screen.py`; `sub_agents/cv_scoring.py`; `cv_score_orchestrator.py` | Focused tests assert the actual third runner argument. |
| R2 | **Fixed.** `Role.job_spec_text` is the sole canonical base; no agent path falls back to marketing description or the removed legacy column. Active RoleIntent and teach exemplars are rendered deterministically by the shared resolver, so recruiter signal is preserved and invalidates the cache consistently. | resolved | `role_requirement_service.py`; both sub-agents; `pre_screening_service.py`; `cv_score_orchestrator.py` | Tests cover no-description fallback and stable overlay serialization. |
| R3 | **Fixed safely.** Copy-paste detection remains visible in structured evidence but defaults to neutral review flagging and does not mutate the score. Hard cap is explicit policy opt-in only. | resolved | `FRAUD_COPY_PASTE_ACTION`; service + sub-agent | Tests cover both default flag and opt-in cap behavior. |
| R4 | **Fixed.** Unicode-aware letter/number tokenization replaces the ASCII-only regex; non-Latin overlap is covered by regression. | resolved | `fraud_detection.py` | Arabic/Cyrillic-style input no longer silently bypasses evaluation. |
| R5 | **Fixed.** Holistic computes/persists the bounded timeline and unverified-claim integrity layer and applies it by default under a cache-keyed rollout flag. | resolved | `holistic.py`; `HOLISTIC_INTEGRITY_PENALTY_ENABLED` | Shadow/applied/fail-open/default-on tests cover the policy. |
| R6 | **Transient pre-screen error hard-stops scoring for up to 6h** (false-reject-by-delay), with a stale docstring claiming the opposite. Deliberate trade against the 7,668-burned-call incident, but it converts an infra blip into a multi-hour gap. | medium | `runner_pre_screen.py:282-291`; `cv_score_orchestrator.py:590-616`; `pre_screening_service.py:451` | Distinguish transient (rate-limit/5xx/timeout) from deterministic errors; short bounded retry for transient; fix the docstring. |
| R7 | **Fixed.** The gate never reads `pre_screen_score_100` or raw evidence as a decision fallback. Missing genuine state fails open to the full scorer; calibration excludes contaminated legacy fallback values. | resolved | `cv_score_orchestrator.py`; `prescreen_gate_calibration.py`; `auto_reject.py` | Covered by missing-genuine and separated-score regressions. |
| R8 | **Fixed.** All paths share one converter and preserve `constraint` as `Priority.CONSTRAINT`, matching aggregation/prompt semantics rather than collapsing it to must-have/preferred. | resolved | `role_requirement_service.py`; service/sub-agents/full scorer | Focused regressions inspect the priorities passed to both runners. |

### 3.5 Fairness, bias & legal risk

This remains the highest-stakes dimension for an AEDT. Aggregate measurement now exists; candidate notice/appeal and broader protected-trait policy remain product/legal work.

| # | Finding | Sev | Location | Fix |
|---|---|---|---|---|
| F1 | **Implemented, operationally opt-in.** A scheduled rolling audit measures actual gate pass, fraud-cap pass, and automated-reject survival using only segregated voluntary EEO self-ID. It suppresses small cells, persists no per-person labels, logs 4/5ths violations, exposes aggregate owner/capability reads, and never changes hiring state. Disabled/low-volume states are explicit rather than falsely green. | resolved infrastructure | `prescreen_impact_service.py`; `compliance_tasks.py`; compliance route; migration 175 | Enable only where consent/governance are ready and alert routing consumes violation logs. This is an internal monitor, not a substitute for an independent LL144 audit. |
| F2 | **Fixed by safer default.** Copy-paste is neutrally review-flagged, not score-capped or auto-rejected, unless an operator explicitly opts back into legacy `cap` policy. | resolved | config + pre-screen service/sub-agent | Default and opt-in behavior have regressions. |
| F3 | **Protected-characteristic safeguard exists only in the conversational agent prompt; the deterministic reject path bypasses it.** The gate reads raw CV + Workable answers + recruiter comments ("same weight as the CV") — free text carrying age/nationality/gender/visa signals — with no fairness guardrail (the holistic engine also dropped its demographic-non-inference rule). | medium | `agent_chat/system_prompt.py:146-150`; `pre_screen_decision_emitter.py:12-17`; `prompts_pre_screen.py:57` | Make the protected-characteristic constraint a code-level invariant on the deterministic path (proxy-detection/redaction pre-pass) + the F1 monitor. |
| F4 | **No candidate notice, explanation, or appeal for automated rejections.** Only internal Workable notes/events; no AEDT-used disclosure, job-relevant reason, or human-review route. EU AI Act high-risk + LL144 notice exposure. (Bounded: auto-disqualify is per-role opt-in; default is a HITL card.) | medium | `application_automation_service.py:404-450` (+ absence across services/decision-policy) | For auto-disqualify orgs: record AEDT use, the job-relevant reason in candidate-appropriate language, and a documented human-review/appeal route. |
| F5 | **Transient-error 6h hard-stop = availability-driven adverse effect** with no proactive alert under sustained outage / budget pause. (Low; recruiter-visible via `score_status`, self-healing.) | low | `cv_score_orchestrator.py:590-616` | Alert on cohorts stuck in `pre_screen_errored`; distinguish vendor errors; safe direction is KEEP the candidate. |
| F6 | **Unverified-claim penalty keys on LLM "familiarity,"** systematically under-recognising legitimate awards/journals from non-Western/smaller institutions (national-origin proxy). (Low: soft, fail-open, AND-gated, can't auto-reject.) | low | `prompts_pre_screen.py:59`; `fraud_detection.py:389-395,242-255` | Prefer corroboration-from-CV over model recognition; segment-monitor flag incidence; document the regional-familiarity bias. |

---

## 4. Integrity & fraud modules — proposed

**Unifying principle (from the platform's own rule and the law alike):** integrity signals become a **separate 0–100 axis** (100 = clean) that **never silently mutates the fit/role_fit score**, feed a **bounded soft penalty or a HITL flag**, and **never auto-disqualify on their own**. Cheap-deterministic-first; escalate to one cheap LLM call only on the ambiguous band; bias-monitor before any hard band ships. Everything below restores parity for the holistic org and removes the existing hard-cap auto-reject as the default.

### 4.1 Copy-paste / spec-mirroring & keyword-stuffing — **P2, effort L**

- **Remaining gap.** The existing whole-CV n-gram signal is now Unicode-aware and flag-only by default, but remains verbatim-oriented and is blind to hidden/white-font and metadata keyword stuffing. More advanced evidence still needs calibration before it can be useful.
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

- **Remaining gap.** Timeline + bounded integrity penalties now run on holistic, but `claims_to_verify` still lacks grounded verification. The unverified-claim check can rely on model familiarity instead of cross-checking the suspect claim against verbatim CV evidence, even though a Citations helper already exists.
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

- **Remaining gap.** The highest-risk pieces are safer now: copy-paste defaults to a flag, holistic integrity parity is live, and aggregate adverse-impact monitoring exists. Signals are still fragmented rather than presented as one separately explained integrity axis, and the optional legacy hard-cap policy remains available.
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

The measurement and operational-safety substrate is now present. The next constraint is accumulating governed real-world volume and closing the remaining candidate-compliance UX before expanding automation.

### P0 — Stop the live false-reject and make the costliest error measurable (days; quick wins)
1. **Completed: A7** — holistic `overall` is required; partial emissions retry/fail without persisting a fabricated zero.
2. **Completed: R1/R2/R8** — service, sub-agents and full scorer share canonical role inputs while preserving authored recruiter intent and teach examples.
3. **Completed: A1/A3** — the weekly sampler full-scores actual filtered candidates in shadow; the gate calibrator and divergence monitor consume those rows.
4. **Completed: F1 infrastructure** — opt-in daily aggregate monitoring covers actual gate/fraud-cap/auto-reject outcomes using segregated voluntary EEO data, with small-cell suppression and owner/capability reads.
5. **Completed: A2/A8** — threshold provenance and runtime documentation match executable behavior.

**What to measure:** false-reject rate from the shadow-scored filtered sample; gate-vs-holistic agreement (κ, Brier, ECE — the metrics module already exists in `evals/metrics.py`, it just lacks data); selection-rate impact ratios by segment.

### P1 — Make the cascade correct, fair, and the integrity layer real (1–2 sprints; the core bets)
6. **Calibrate the cheap score and define a borderline band.** Fit a logistic/isotonic map from raw gate score → empirical advance-rate on the collected samples; auto-decide only outside a tuned band, escalate the band to the holistic engine, abstain-early to HITL on un-resolvable cases. Bias conservative toward escalation (overconfidence is the dominant failure). This is what makes the gate both cheaper *and* more accurate.
7. **Run the small-vs-large agreement study** Taali is uniquely positioned to do (it has both engines + recruiter outcomes): bucket by calibrated probability, find the disagreement zone, set the band to exactly that zone.
8. **Part-completed:** R3/F2 flag-only default, R4 Unicode tokenization, and R5 holistic parity are live. The remaining bet is a separately surfaced unified integrity axis plus grounded Tier-2 claim verification in shadow.
9. **Completed: C2** — both holistic calls cache stable role context.

### P2 — Throughput, cost tail, and the net-new integrity modules (larger bets)
10. **Turn the gate on deliberately** (C1) once the band is calibrated and the monitor is live; document the flag.
11. **Completed: S1/S2** — event-driven/fast backlog admission and durable leased Celery fan-out remove the hourly/web-thread bottlenecks.
12. **Closed by decision:** C3 report retention is intentional; C4's unused scoring Batches experiment was removed. Keep Message Batches for latency-tolerant parsing and per-app fan-out for recruiter-visible scoring.
13. **Identity/CV-farm module (4.4)** and **AI-authorship context chip (4.2, zero-weight)** — both behind the bias monitor, flags-only.

### P3 — Compliance posture & per-org policy layer
14. **AEDT compliance scaffolding** (F3/F4): code-level protected-characteristic invariant on the deterministic path; candidate AEDT-disclosure + job-relevant reason + appeal route for auto-disqualify orgs; per-org policy flags for NYC (bias audit + notice) and EU (human oversight + explanation). Extend score-provenance into reject-provenance (inputs, engine_version, threshold, verdict, reason, human override) — this is the Mobley defense record and the EU logging duty in one.
15. **Document the deliberate decision not to use AI-text detection as a screening criterion**, citing the ESL FP rate and EEOC exposure — a compliance/trust asset for the public-API positioning.

---

## 7. Open questions for Sam

Resolved implementation decisions: holistic integrity parity is on by default, copy-paste is flag-only by default, Unicode tokenization is live, and the recruiter report is intentionally retained. Remaining decisions:

1. **Should any org ever run silent auto-disqualify, or should every reject route through a human?** This determines the required notice, explanation, appeal, and oversight surface.
2. **Who owns monitor activation and response?** `PRESCREEN_ADVERSE_IMPACT_MONITOR_ENABLED` must be paired with consent/governance, enough voluntary EEO volume, an alert recipient, and a documented investigation/rollback procedure.
3. **Ground truth ownership:** who labels the first representative cohorts and roles, including known-good candidates the gate would otherwise filter? The shadow pipeline exists; evidence quality now depends on this operating process.
4. **Where should anti-fraud investment go:** grounded claim verification + assessments (defensible and capability-based) versus additional detection heuristics (cheap but legally fraught)? AI-authorship detection should remain omitted from decisions.
5. **Per-org compliance flags vs. a global policy:** should NYC-linked orgs enforce audit/notice/alternative-process workflow and EU-linked orgs enforce human oversight/explanation as organization-scoped settings?
6. **Identity/liveness:** is an identity-verification seam worth the added privacy/FCRA/consumer-reporting analysis before customers request it?
