# Phase 7 Summary — Cohort planner (agent surveys, reasons, acts)

> Historical implementation note. This file records the original Phase 7
> rollout and its then-current approval defaults; it is not the operating
> contract. The current one-click autonomy, metering, recovery, and HITL
> boundaries are defined in [the agent decision matrix](../agent-decision-matrix.md).
> The manual checks at the end are release-validation notes, not routine steps
> required after turning on a role.

## Mental-model shift

Phases 1-6 wired the agent as a **reactive** system: per-application Celery events fired the orchestrator, which thought about one candidate at a time. Phase 7 inverted the trigger model so the agent owns the cycle. The original rollout used a 30-minute Beat tick; the current production cadence is hourly, with immediate activation/resume and event wakeups. The orchestrator surveys the cohort, decides where the leverage is, and acts.

This aligns with the design principles in:
- **Anthropic — "Building effective agents":** workflows have predefined paths; agents direct their own. The previous trigger model was workflow-shaped (event → cycle); cohort planner is agent-shaped (the agent decides where to spend its cycle).
- **OpenAI — "A practical guide to building agents":** maximize a single agent's capabilities first; new agents add complexity. Phase 7 is **single-agent + sharper tools**, not a new orchestration layer or sub-agent fanout.

What I deliberately did NOT build (and the OpenAI guide explicitly cautions against):

- ❌ A `cohort_planner` module with explicit auto / HITL / ask-recruiter lanes — that's a workflow dressed as an agent.
- ❌ A `ProposedWork` shape + `propose_work()` Protocol method on every sub-agent — needless ceremony.
- ❌ A new `cv_fetcher` sub-agent — that's a tool action, not a domain specialist.
- ❌ A `role_state_scanner` separate module — collapses into a single tool.

Net delta from the design I sketched: ~700 lines instead of ~1500.

## What shipped

### Backend

- `backend/alembic/versions/067_add_agent_needs_input_and_send_assessment_hitl.py`
   - New `agent_needs_input` table (open recruiter questions).
   - `roles.agent_send_assessment_requires_approval` column (default True — safer cohort-era stance).
- `backend/app/models/agent_needs_input.py` — `AgentNeedsInput` model with `NEEDS_INPUT_KINDS` constant.
- `backend/app/models/role.py` — adds the HITL toggle column.
- `backend/app/actions/ask_recruiter.py` — three pure functions:
  - `open(...)` — agent-only, idempotent on (role_id, kind).
  - `answer(...)` — recruiter-only.
  - `dismiss(...)` — either party, idempotent.
- `backend/app/agent_runtime/cohort_tools.py` — three diagnostic helpers:
  - `survey_role_state` — counts in each pipeline state + role-config gaps + open recruiter questions.
  - `find_apps_in_state` — id list for one state.
  - `read_pending_recruiter_inputs` — open + recently-resolved questions.
- `backend/app/agent_runtime/tool_registry.py`:
  - **New tools** registered: `survey_role_state`, `find_apps_in_state`, `read_pending_recruiter_inputs`, `batch_score_cv`, `ask_recruiter`.
  - `_tool_send_assessment` now respects `Role.agent_send_assessment_requires_approval` — when on, instead of auto-sending it opens an `agent_needs_input` row and the recruiter approves on the role page.
- `backend/app/agent_runtime/system_prompt.py`:
  - PROMPT_VERSION → `agent.v6.cohort-planner.2026-05-08`.
  - The prompt teaches the agent the **survey → reason → act** loop:
    1. Always pair `survey_role_state` + `read_pending_recruiter_inputs` in one round-trip.
    2. Decide where to spend the cycle from the survey output.
    3. Auto-execute deterministic work (`batch_score_cv`); ask the recruiter for genuine gaps; queue verdicts via `evaluate_policy` for human-in-loop decisions.
- `backend/app/services/application_events.py` — per-application agent trigger removed (kept the audit-write path; just no longer enqueues a Celery task per applicant).
- `backend/app/tasks/agent_tasks.py` — added `agent_cohort_tick_sweep` + `agent_cohort_tick_role`; the current Beat entry is `agent-cohort-tick-every-60-minutes` (3600s).
- `backend/app/agent_runtime/needs_input_routes.py` — `/api/v1/agent-needs-input` HTTP surface (list + answer + dismiss).
- `backend/app/main.py` — wires the new router.

### Frontend

- `frontend/src/features/jobs/AgentNeedsInputCard.jsx` — inline card on the role page. Hides itself when there are no open questions; renders option-buttons or a free-text answer field per row; supports dismiss.
- `frontend/src/features/jobs/JobPipelinePage.jsx` — mounts the card at the top of the cockpit pane.
- `frontend/src/index.css` — styles for the card.

## Tests

`backend/tests/cohort_planner/`:
- `test_cohort_tools.py` — 8 cases (empty role, state classifications, intent gaps, source-grep guard for state dispatch).
- `test_ask_recruiter_action.py` — 7 cases (open/answer/dismiss; idempotency; actor enforcement; unknown kind rejected).
- `test_send_assessment_hitl.py` — 2 cases (HITL gate opens needs_input row; toggle off auto-executes).

132 backend tests pass overall (Phase 1-7 + adjacent agent_runtime suite). Golden eval harness still 7/7.

## Tool taxonomy (per OpenAI guide §Tools)

| Tool | Type | Risk |
|---|---|---|
| `survey_role_state`, `find_apps_in_state`, `read_pending_recruiter_inputs` | Data | Low |
| `get_application`, `get_candidate`, `get_candidate_cv`, `search_applications`, `compare_applications`, `get_cohort_signals`, `nl_search_candidates`, `graph_search_candidates` | Data | Low |
| `score_cv`, `batch_score_cv` | Action | Low (idempotent, cached) |
| `evaluate_policy` | Data (deterministic verdict) | Low |
| `send_assessment` | Action | **High** — gated by `Role.agent_send_assessment_requires_approval` |
| `queue_advance_decision`, `queue_reject_decision`, `queue_skip_assessment_reject_decision` | Action | **High** — always recruiter approval |
| `ask_recruiter` | Action | Low (creates a question, not a side effect) |
| `agent_run_complete` | Terminal | n/a |

## What's NOT in this phase (intentional)

- **No CLI / shell access to the agent.** OpenAI's guide rates tool risk as a function of reversibility and blast radius — a recruiting agent doesn't need filesystem access.
- **No MCP transport changes.** The existing `app/mcp/` server stays as the read-only external surface for Claude Desktop / Cursor; the in-app orchestrator continues to use native function calling. Same handlers, two transports — the right shape per both guides.
- **No additional sub-agents.** At the time, Phase 7 added cohort tools rather
  than specialists. The later canonical five are `pre_screen`, `cv_scoring`,
  `assessment_scoring`, `graph_priors`, and `task_selection`; the unregistered
  `intent_parser` execution path was superseded by `RoleIntent`. Its import path
  now exposes only a provider-free, fail-closed compatibility facade.
- **No new orchestration framework.** The single-agent loop with `MAX_TOOL_ROUNDS` already gives the orchestrator stopping conditions. Anthropic's guide is explicit: "reduce abstraction layers and build with basic components."

## Validation

- All 132 backend tests pass (17 new + 115 existing).
- Golden eval harness 7/7.
- Frontend builds clean; no test regressions in `vitest run`.

## Followups for Sam

1. Walk through the role page on a real agent-on role and confirm the AgentNeedsInputCard renders only when there's an actual question.
2. Manually trigger `agent_cohort_tick_role.delay(role_id)` from a Python REPL on a role with mixed state and inspect the AgentRun reasoning summary.
3. Cadence was tuned to `agent-cohort-tick-every-60-minutes`; activation, resume, and application events provide the immediate path.
