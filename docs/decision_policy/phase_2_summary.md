# Phase 2 Summary — Sub-agent contracts

> Historical note (July 2026): the unregistered `intent_parser` execution path
> was retired after `RoleIntent` became the sole production source. A
> provider-free, fail-closed compatibility facade and safety tests preserve the
> import/schema contract. Pricing retains its event category so historical
> usage can still be recomputed.

## What shipped

- `backend/app/sub_agents/__init__.py` — package surface; auto-registers v1 sub-agents on import.
- `backend/app/sub_agents/base.py` — `SubAgentRequest`, `SubAgentResult`, `SubAgent` Protocol.
- `backend/app/sub_agents/registry.py` — in-process registry (`register_sub_agent`, `get_sub_agent`, `all_sub_agents`).
- `backend/app/sub_agents/pre_screen.py` — wraps `cv_matching/runner_pre_screen.run_pre_screen`. Fast-path: cached score on `CandidateApplication.pre_screen_score_100`.
- `backend/app/sub_agents/cv_scoring.py` — wraps `cv_matching/runner.run_cv_match`. Fast-path: cached `cv_match_details` on the application.
- `backend/app/sub_agents/intent_parser.py` — historical provider-free compatibility facade; never registered.
- `backend/app/sub_agents/assessment_scoring.py` — read-side wrapper of cached `taali_score_cache_100` + `assessment_score_cache_100`.

## Tests

`backend/tests/sub_agents/`:
- `test_pre_screen_sub_agent.py` — 4 cases (cache fast-path skips Claude; missing CV → error; runner invoked when no cache; unknown app → error).
- `test_cv_scoring_sub_agent.py` — 3 cases (cache fast-path; missing CV; skip_cache invokes runner).
- `test_intent_parser_sub_agent.py` — verifies fail-closed execution, schema parsing, and the canonical-five registry invariant.
- `test_assessment_scoring_sub_agent.py` — 2 cases (cached scores returned; no assessment → confidence=0).
- `test_registry.py` — 3 cases (all four v1 sub-agents register; lookup; unknown raises).

## Key decisions made in-band

- `intent_parser` collapsed into a sub-agent rather than living inside the orchestrator (matches the Phase 3 spec's `parse_intent` MCP tool surface). Justification: it's an LLM call with a discrete cache key, distinct from orchestrator planning.
- Sub-agents are **read-mostly** — they report cached state when available, fall back to Claude on cold path. They do NOT mutate the application beyond what the underlying runner does (cv_matching runners write to `cv_score_cache` themselves).
- `SubAgentRequest.extra` slot lets the orchestrator pass the four intent slots (`must_have`, `preferred`, `nice_to_have`, `constraints`) without bloating the public signature.

## What was skipped vs spec

- `graph_priors` deferred to Phase 4 per spec.

## Validation

- All 16 sub-agent tests pass.
- All 4 expected sub-agents register on `app.sub_agents` import.
