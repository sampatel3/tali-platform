# EU AI Act Annex III — Conformity package for cv_matching

**Status:** drafted by engineering for legal review (RALPH 4.7).
Replace this banner once a qualified employment-law practitioner
has signed off.

## Classification

The cv_matching pipeline is a **high-risk AI system** under EU AI
Act Annex III §4(a): "AI systems intended to be used for the
recruitment or selection of natural persons, in particular for
placing targeted job advertisements, analysing or filtering job
applications, or evaluating candidates."

Effective date: high-risk obligations apply 24 months after entry
into force. Cv_matching has been pre-emptively designed to meet
the obligations because (a) NYC LL144 already requires similar
controls in our launch market and (b) retrofitting a deployed
system is materially harder than building it in.

## Annex III §4 obligations and how this pipeline meets them

### (i) Risk management system (Art. 9)

Implemented as the four-phase rollout in `RALPH_TASK.md`:

- Each phase has a [HUMAN REVIEW] gate (1.10, 2.12, 3.12, 4.8).
- No prompt version is promoted without the eval harness diff in
  [`evals/baseline_results/`](../backend/app/cv_matching/evals/baseline_results/).
- Per-PR fairness gate via the counterfactual probes in
  [`.github/workflows/fairness.yml`](../.github/workflows/fairness.yml)
  blocks regressions before they ship.

### (ii) Data and data governance (Art. 10)

- Training data for the calibrators is sourced exclusively from
  recruiter override events captured by
  [`POST /candidates/{id}/cv-match-override`](../backend/app/cv_matching/routes.py).
- Embedding vectors are stored hash-keyed
  ([`cv_embeddings`](../backend/app/models/cv_embeddings.py)); the
  CV plaintext is held only as long as the underlying CandidateApplication.
- No protected-class attributes are inferred from the CV
  (validated by Rule 2 in
  [`prompts.py`](../backend/app/cv_matching/prompts.py) and by the
  counterfactual probes in
  [`fairness/probes.py`](../backend/app/cv_matching/fairness/probes.py)).

### (iii) Technical documentation (Art. 11 and Annex IV)

- Architecture documented in
  [`docs/cv_matching_audit.md`](cv_matching_audit.md) and
  [`docs/cv_matching_cutover.md`](cv_matching_cutover.md).
- Aggregation contract in
  [`backend/app/cv_matching/calibration.md`](../backend/app/cv_matching/calibration.md).
- Per-archetype rubrics in
  [`backend/app/cv_matching/rubrics/`](../backend/app/cv_matching/rubrics/).
- Phase-by-phase migration log in `RALPH_TASK.md`.

### (iv) Record-keeping / logs (Art. 12)

- Every match emits a trace row via
  [`telemetry.py`](../backend/app/cv_matching/telemetry.py)
  carrying trace_id, content hashes, prompt version, model version,
  token usage, latency, retry count, validation failures, cache
  status, and final status. Shadow runs are flagged.
- Recruiter overrides are append-only audit rows in
  [`cv_match_overrides`](../backend/app/models/cv_match_override.py).
- Calibrator snapshots are written timestamped under
  [`calibrators/snapshots/`](../backend/app/cv_matching/calibrators/).

### (v) Transparency and information for users (Art. 13)

- The recruiter UI shows both the raw rubric score (explainability)
  and the calibrated `P(advance)` (primary ranking signal). See
  [`calibrators/README.md`](../backend/app/cv_matching/calibrators/README.md).
- Per-requirement evidence quotes are surfaced verbatim in the
  cv_match_details JSON; downgrades for hallucinated quotes are
  recorded by [`validation.py`](../backend/app/cv_matching/validation.py).

### (vi) Human oversight (Art. 14)

- Recruiters can override every recommendation. Overrides feed the
  calibrators on a weekly cycle.
- Cases where the conformal interval crosses the decision boundary
  are flagged ``requires_human_review = True`` (see
  [`fairness/conformal.py`](../backend/app/cv_matching/fairness/conformal.py))
  and the recruiter UI surfaces these to a separate review queue.
- The recruiter SOP is written in
  [`docs/recruiter_oversight_sop.md`](recruiter_oversight_sop.md)
  *(TODO: write this companion doc before legal review)*.

### (vii) Accuracy, robustness, cybersecurity (Art. 15)

- Accuracy: agreement metrics (Krippendorff α, Cohen's κ, Spearman ρ,
  Brier, ECE) are computed on every prompt version change against
  the golden cases in
  [`evals/golden_cases.yaml`](../backend/app/cv_matching/evals/golden_cases.yaml).
  Phase 3 success criteria require α ≥ 0.667 and ECE ≤ 0.05.
- Robustness: Population Stability Index drift detection
  ([`fairness/drift.py`](../backend/app/cv_matching/fairness/drift.py))
  fires nightly; PSI > 0.25 alerts.
- Cybersecurity: prompt-injection mitigation via the UNTRUSTED_CV
  spotlighting wrapper (Microsoft pattern, 2024) plus the
  heuristic injection scanner in
  [`validation.py`](../backend/app/cv_matching/validation.py).

## Post-market monitoring plan (Art. 17)

- **Daily:** trace-row tailing via the
  [`/admin/cv-match/traces`](../backend/app/cv_matching/routes.py)
  endpoint. Operations grep for `final_status=failed` rates above
  baseline and ECE-alert log lines.
- **Weekly:** recalibration job
  ([`calibrators/recalibrate.py`](../backend/app/cv_matching/calibrators/recalibrate.py))
  runs and reports per-(role_family, dimension) ECE.
- **Nightly:** PSI drift check
  ([`fairness/drift.py`](../backend/app/cv_matching/fairness/drift.py))
  for every active role family.
- **On every PR touching prompts/aggregation/validation/runner/rubrics:**
  counterfactual fairness gate
  ([`fairness.yml`](../.github/workflows/fairness.yml))
  blocks merge on flip rate > 5% or |Δscore| > 0.05.

## Data Protection Impact Assessment (DPIA) — template

A DPIA must be completed and reviewed annually (or on material
change). The template lives at
[`docs/dpia_template.md`](dpia_template.md)
*(TODO: write this companion doc before legal review)*.

Fill out:

1. **Necessity and proportionality** — why does each data field
   need to flow through cv_matching?
2. **Identified risks** — discrimination, opacity, data leakage.
3. **Mitigations** — counterfactual probes, calibration to
   recruiter intuition, evidence-grounding, override capture.
4. **Residual risk acceptance** — recorded sign-off by Sam + legal
   counsel + a representative from the recruiting team.

## Recruiter human-oversight SOP — template

Before a v4 model version goes live in a market with mandatory
oversight (EU, NYC), the recruiter SOP must explicitly document:

- When the system MUST be overridden (e.g. when
  `requires_human_review = True`).
- When overrides are EXPECTED rather than rare (e.g. for a role
  family with < 50 prior overrides — calibrators are unreliable
  there).
- How disagreement with the system is logged.
- Escalation path for systematic miscalibration patterns.

Living in [`docs/recruiter_oversight_sop.md`](recruiter_oversight_sop.md)
*(TODO: write this companion doc before legal review)*.

## Open items before submission

1. Write `docs/dpia_template.md` and complete the first DPIA.
2. Write `docs/recruiter_oversight_sop.md`.
3. Confirm with legal counsel that NYC LL144 sub-§5(c) (independent
   bias audit) overlaps acceptably with this conformity pack so we
   don't run two parallel audits.
4. Decide whether the EU AI Act registration database entry happens
   pre-launch or at the obligation effective date — counsel call.
