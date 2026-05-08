# Phase 1 Summary — DecisionPolicy schema, engine, bootstrap

## What shipped

- `backend/alembic/versions/066_add_decision_policies.py` — migration creating `decision_policies` table + indexes; backfills one bootstrap row per existing org.
- `backend/app/models/decision_policy.py` — `DecisionPolicy` SQLAlchemy model.
- `backend/app/decision_policy/__init__.py` — package surface re-export.
- `backend/app/decision_policy/schema.py` — Pydantic v2 `PolicyJson` (extra=forbid). Validates: schema_version, decision_point names, rule actions, weights sum to 1.0 ±0.01.
- `backend/app/decision_policy/engine.py` — pure-Python `evaluate(inputs, *, db) -> PolicyDecision`. Three decision points walked in fixed order; first non-skip/no_action verdict wins. Rule conditions parsed by a tiny tokenizer (AND / OR / comparisons). Confidence = fraction of weighted signals present in inputs.
- `backend/app/decision_policy/intent.py` — `apply_intent_overrides(policy, intent)` strictness-modifier overlay; immutable, returns new policy.
- `backend/app/decision_policy/bootstrap.py` — `bootstrap_org` (idempotent ORM) + `bootstrap_all_orgs_via_connection` (raw SQL for migration time). Resolves `role_fit_min` from median of `Role.score_threshold`, falling back to `Organization.default_score_threshold` then to a constant.
- `backend/app/decision_policy/evals/` — golden eval harness (`golden_cases.yaml` + `run_evals.py`). 7/7 cases pass.

## Tests

`backend/tests/decision_policy/`:
- `test_schema_validation.py` — 6 cases (rejects bad weights, unknown points/actions, extras).
- `test_bootstrap_idempotent.py` — 5 cases (idempotency, threshold resolution).
- `test_engine_hard_rules.py` — 3 cases (must_have_blocked, recruiter-action skip).
- `test_engine_weighted_scoring.py` — 5 cases (queue paths for all three points).
- `test_engine_intent_overlay.py` — 6 cases (strictness modifier shifts within cap).
- `test_engine_rule_path.py` — 3 cases (rule_path is human-readable, no policy → no_action).

## Key decisions made in-band

- `cause` field in bootstrap revisions uses `human_edit` rather than introducing a new `bootstrap` value — staying compatible with the existing `REVISION_CAUSES` tuple. Notes field carries the disambiguation. Phase 5 widens REVISION_CAUSES if/when needed.
- Confidence is `count(present_signals) / count(weighted_keys)` — signal density, not signal value. A confident reject of a 5/100 candidate reads the same way as a confident advance of a 95/100 candidate.
- `decision_points` in `policy_json` is a dict keyed by name; the engine walks `DECISION_POINT_ORDER` (send_assessment > advance > reject) and returns the first non-skip/no_action verdict. A skipped point cascades to the next.
- Reject point gets a single `role_fit_score` weight so the engine can compute confidence; without it confidence collapses to 0 and the floor blocks queueing.

## What was skipped vs spec

- Per-role policy seeding: bootstrap only writes the org-default row (per CLAUDE.md §10.3). Role-specific overrides will be added through the Hub later.

## Validation

- All 28 unit tests pass.
- Golden eval harness: 7/7 pass.
- Manual sanity: bootstrap on a real org produces thresholds within ±5 points of the existing `Role.score_threshold` median.
