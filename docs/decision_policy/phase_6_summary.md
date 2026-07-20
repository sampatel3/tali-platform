# Phase 6 Summary — Hub UI (historical)

> This document records what the original Phase 6 delivery introduced. It is
> not a current component inventory. `DecisionExplainer.jsx` was later retired
> after it became disconnected from every application entry point. Current
> recruiter-facing decision explanations are rendered by
> `shared/decisions/AgentDecisionCard.jsx` and `DecisionNarrative.jsx`.

## What shipped

### Backend

- `backend/app/decision_policy/routes.py` — admin-only HTTP surface mounted at `/api/v1/admin/decision-policy`:
  - `GET /` — active policy + last-50 revision timeline.
  - `GET /pending` — inactive `cause='feedback_retune'` policies awaiting activation, with diff annotations.
  - `POST /{policy_id}/activate` — flips activation + deactivates predecessor in one transaction.
  - `POST /{policy_id}/discard` — soft-discard (sets `deactivated_at`).
  - `GET /signals?days=N` — per-day teach / override / manual disagreement counts + top failure modes + manual-action vs agent-decision volume.
- `backend/app/main.py` (modified) — wires the router at `/api/v1`.

### Frontend

- `frontend/src/features/decision_policy/api.js` — axios client.
- `frontend/src/features/decision_policy/PolicyView.jsx` — active policy + decision-points table + revision timeline.
- `frontend/src/features/decision_policy/PendingRetuneReview.jsx` — diff cards with Activate / Discard buttons.
- `frontend/src/features/decision_policy/SignalsDashboard.jsx` — daily-bucket table + top failure modes + manual vs agent volume summary.
- `frontend/src/features/decision_policy/DecisionExplainer.jsx` — originally shipped as an embeddable component for AgentDecision panels; subsequently retired when the live decision surfaces consolidated on the shared decision card and narrative components described above.
- `frontend/src/features/decision_policy/DecisionPolicyPage.jsx` — tabbed page wrapping the three views.
- `frontend/src/AppShell.jsx` (modified) — lazy import + route at `/admin/decision-policy/*`.

## Tests

- `backend/tests/decision_policy/test_routes.py` — 4 cases (active policy fetch, pending list empty, activate flow, signals fetch).
- `frontend/src/features/decision_policy/DecisionPolicyPage.test.jsx` — 1 case (page renders + active policy fetched + decision points surfaced).

## Key decisions made in-band

- No charting library introduced — the daily counts surface as a plain table. Recruiters have asked for "is the agent improving?" semantics, which the table satisfies; charts are a v2 polish.
- At Phase 6 delivery time, `DecisionExplainer` was a pure component (no API call) that consumed `evidence` from the AgentDecision row directly. This is historical design context; the component is no longer shipped.
- Activate / Discard endpoints are admin-only via the existing `is_superuser` gate.

## What was skipped vs spec

- Per-revision drilldown view (clicking a timeline row) — left as a v2 follow-up; the timeline shows cause + notes + feedback_ids inline which is enough for v1.
- The disagreement chart over time — see "no charting library" above.

## Validation

- All 4 new backend route tests pass.
- Frontend page test passes (`vitest run src/features/decision_policy/DecisionPolicyPage.test.jsx`).
- Full backend suite (decision_policy + sub_agents + agent_runtime_policy + adjacent agent_runtime tests): 110/110 pass.
