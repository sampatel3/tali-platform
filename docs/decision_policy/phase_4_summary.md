# Phase 4 Summary — Graph priors at decision time

## What shipped

- `backend/app/sub_agents/graph_priors.py` — `GraphPriorsSubAgent` composing existing `candidate_graph.search` adapters (`colleague_neighbourhood` + `candidate_ids_matching_all`). No new Cypher / Graphiti queries.
- Algorithm:
  1. Pull candidate's neighbourhood payload (graph search).
  2. Translate company anchors into `GraphPredicate(worked_at, …)` and intersect across them.
  3. Filter to same-role-family candidates using `cv_matching/calibrators/extractor._default_role_family_mapper`.
  4. For each remaining neighbour, look up `application_outcome` + apply time decay (`weight = max(0, 1 - days/decay_days)`).
  5. `p_advance = sum(weight * advanced_label) / sum(weight)`.
  6. Cold-start: when `effective_neighbour_count < min_neighbours_for_prior`, returns `confidence=0` so the engine collapses the prior weight to zero cleanly.
- Per-cycle in-memory cache keyed by `(application_id, role_id)` — `clear_cycle_cache()` exposed for orchestrator boundaries.
- Wired into `agent_runtime/policy_evaluator.PRE_EVAL_SUB_AGENT_NAMES` so the Phase 3 `evaluate_policy` tool now includes graph priors automatically.

## Tests

`backend/tests/sub_agents/test_graph_priors_sub_agent.py`:
- `test_cold_start_returns_zero_confidence` — empty graph → confidence=0.
- `test_priors_compute_p_advance_from_neighbours` — 8 neighbours, 6 advanced → p_advance ≈ 0.75.
- `test_priors_filter_to_same_role_family` — different-family candidates excluded from prior calc.
- `test_priors_use_only_existing_search_apis` — source-grep guard: only `colleague_neighbourhood` + `candidate_ids_matching_all`; no `.driver`, no Graphiti search invocations.

## Key decisions made in-band

- Predicates: only `worked_at` company anchors used for intersection. Schools/skills are too broad and would dilute the cohort.
- `p_hired == p_advance` in v1 — same proxy. When we have a non-trivial sample of `application_outcome='hired'` rows we can split.
- Cycle cache is module-global with a lock; `clear_cycle_cache()` exposed for the orchestrator to call at cycle entry. Phase 5 retune integration calls this.
- Confidence formula: `min(1.0, effective_count / (2 * min_neighbours_for_prior))` — saturates at twice the floor.

## Validation

- All 4 graph_priors tests pass.
- Eval harness still 7/7.
- Verified the source-grep guard catches the desired surface (no Cypher, no `.driver`).
