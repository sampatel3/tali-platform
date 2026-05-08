# Phase 3 Summary — Orchestrator + manual-action awareness

## What shipped

- `backend/app/agent_runtime/manual_action_reader.py` — `read_recent_manual_actions(db, application_id, lookback_hours)`. Pulls `actor_type='recruiter'` events; classifies by `event_type` into ManualAction kinds (`sent_assessment`, `rejected`, `advanced`, `advanced_outcome`).
- `backend/app/agent_runtime/policy_evaluator.py` — `evaluate_for_application(db, role, application_id)`. Gathers sub-agent outputs (pre_screen, cv_scoring, assessment_scoring; Phase 4 added graph_priors), reads manual actions using the policy's own `manual_action_window.lookback_hours`, builds `DecisionInputs`, calls `engine.evaluate`. Returns `(verdict, sub_agent_outputs)`.
- `backend/app/agent_runtime/tool_registry.py` (modified):
  - New `evaluate_policy` Anthropic tool — bridges sub-agents → engine.
  - `_stamp_policy_revision_in_evidence` augments evidence dicts with `policy_revision_id` automatically before queueing.
  - Structured log emission: `taali.policy.evaluation` logger fires per evaluation with org / role / decision_type / confidence / intent / manual flags.
- `backend/app/agent_runtime/system_prompt.py` (modified):
  - PROMPT_VERSION bumped to `agent.v5.policy-aware.2026-05-08`.
  - System prompt updated to describe `evaluate_policy` and require it before any `queue_*` tool.

## Tests

`backend/tests/agent_runtime_policy/`:
- `test_manual_action_reader.py` — 6 cases (assessment send, agent actor ignored, outcome → rejected, stage → advanced, lookback exclusion, zero lookback).
- `test_policy_evaluator.py` — 3 cases (strong candidate → queue_send; recent manual send skips; missing app).
- `test_queue_evidence_stamp.py` — 3 cases (policy_revision_id added; existing wins; None evidence handled).

Plus the full existing `test_agent_runtime_*.py` suite (24 tests) re-runs cleanly.

## Key decisions made in-band

- The orchestrator stays as-is — `evaluate_policy` is added as a tool the LLM agent invokes, rather than rewriting the existing `run_cycle` loop. Justification: minimises blast radius; the agent gets a deterministic verdict surface to anchor against; existing budget / telemetry / audit machinery untouched.
- `_stamp_policy_revision_in_evidence` runs at queue time, not at evaluate time — so even if the agent forgets to thread the rev_id through, the audit row still has it.
- Manual-action lookback window is read from the active policy's own `manual_action_window.lookback_hours` so a retune that widens / narrows it takes effect without code changes.

## What was skipped vs spec

- The orchestrator's `run_cycle` is NOT rewritten end-to-end — kept the existing budget-bounded multi-round loop and added `evaluate_policy` as a tool. The system prompt change nudges the agent to call it before queueing, and `_stamp_policy_revision_in_evidence` ensures policy attribution lands even on agent confusion.

## Validation

- All 12 new agent_runtime_policy tests pass.
- Full agent_runtime_orchestrator + tools tests still pass (24 tests).
- Eval harness (Phase 1) re-run: 7/7 pass.
