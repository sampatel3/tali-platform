# Phase 5 Summary — Feedback aggregation + retune loop

## What shipped

- `backend/app/decision_policy/feedback_aggregator.py` — `aggregate_signals(db, organization_id)` returns a uniform `(signal_type, weight, disagreement_pattern, source_id)` list across:
  - `decision_feedback` rows (weight 1.0; co-sign for `scope='org'` respected).
  - `agent_decisions` with `human_disposition='overridden'` and no attached feedback row (weight 0.3).
  - Manual recruiter `CandidateApplicationEvent`s (weight 0.8) — passed through retroactive eval; agreements dropped.
  - Per-org weight overrides via `Organization.workspace_settings.decision_policy_signal_weights`.
- `backend/app/decision_policy/retroactive_eval.py` — `disagreement_for_manual_event(db, event=event)` reconstructs `DecisionInputs` from cached scores on the application and runs the **current** active policy. Returns one of: `manual-send-on-would-reject`, `manual-reject-on-would-send`, `manual-advance-on-would-reject-post-assessment`, `manual-reject-on-would-advance`, or `agreement`.
- `backend/app/decision_policy/retuner.py` — class-based `Retuner` Protocol + `HeuristicRetuner` v1 implementation:
  - Per-pattern threshold shifts using `tanh(weighted_count / 5.0)` so magnitude saturates fast.
  - `failure_mode='wrong_threshold'` teach signals get a 1.5× magnitude bump.
  - `failure_mode='missing_signal'` bumps `weights.graph_prior_p_advance` by 0.05 (capped at 0.4); other weights renormalised so sum stays 1.0.
  - All shifts capped at `MAX_SHIFT_PER_DIMENSION = 5.0`.
  - Returns `None` when `weighted_total < MIN_SIGNALS_FOR_RETUNE` (default 10).
- `backend/app/decision_policy/diff.py` — `policy_diff(old, new, *, proposal=None)` recursive flat-key diff; metadata changes excluded; retuner-supplied `cause_summary` annotations attached when present.
- `backend/app/decision_policy/nightly_retune.py` — `run_for_org`/`run_for_all_orgs` glue: aggregate → retuner → write `RubricRevision (cause='feedback_retune')` + new `DecisionPolicy` row (inactive by default; auto-applies when `Organization.workspace_settings.decision_policy_auto_apply=True`). Skips orgs with no agent runs in the last 7 days.
- `backend/app/tasks/decision_policy_tasks.py` — `nightly_retune_sweep` Celery task.
- `backend/app/tasks/celery_app.py` (modified) — beat schedule entry `decision-policy-nightly-retune` running daily.

## Tests

`backend/tests/decision_policy/`:
- `test_feedback_aggregator.py` — 3 cases (three sources surface; default weights; unsigned org-scope deferred).
- `test_retroactive_eval.py` — 4 cases (agreement; recruiter-send when policy would reject; recruiter-reject when policy would send; non-recruiter event ignored).
- `test_retuner.py` — 5 cases (skip below min_signals; loosen on manual-send pattern; cap at max_shift_per_dimension; missing_signal bumps graph weight; metadata records provenance).
- `test_diff.py` — 3 cases (unchanged → empty; threshold change surfaces; metadata-only excluded).
- `test_nightly_retune.py` — 3 cases (skip when no recent runs; inactive by default; auto-apply flips activation + deactivates predecessor).

## Key decisions made in-band

- The retuner is class-based to make swapping in a learned implementation purely additive in v2.
- Magnitude function uses `tanh` (not raw count) so a flood of signals never overshoots — by the time weighted_count > ~20, magnitude is essentially pinned at the cap.
- Auto-apply is per-org opt-in; default is inactive-and-notify so admins eyeball every retune in the Hub.
- `wrong_threshold` failure direction defaults to `loosen` — most common recruiter complaint is "agent is too strict". Phase 6 UI captures explicit direction.

## Validation

- All 18 new tests pass (aggregator 3 + retroactive 4 + retuner 5 + diff 3 + nightly 3).
- Full decision_policy suite: 46 tests pass.
- Eval harness still 7/7.
