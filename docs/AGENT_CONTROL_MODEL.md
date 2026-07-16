# Agent control model

Taali has two independent control layers:

1. **Workspace control** is an emergency/operational hold across every role in
   the workspace. Only a workspace owner can change it.
2. **Role control** is the desired state of one role. It can be off, locally
   paused, or ready to run.

The workspace control is an overlay. It never bulk-edits role controls. This
distinction is what makes a workspace resume safe: it removes only the
workspace hold and reveals each role's saved state.

## Effective state

| Saved role state | Workspace running | Workspace paused |
| --- | --- | --- |
| Off | Off | Off |
| On, locally paused | Role paused | Held by workspace; remains role-paused after resume |
| On, locally runnable | Running | Held by workspace; resumes automatically after resume |

Precedence is therefore `off > workspace hold > local role hold > running`.
The API returns both the effective state and the saved role state so the UI can
explain what will happen next instead of flattening the two controls together.

## Commands

### Pause workspace

- Requires workspace-owner permission.
- Records actor, timestamp, reason, request ID, and a new workspace control
  version in append-only audit history.
- Stops autonomous admission for all enabled roles without changing their
  local pause flags, review queues, or configured budgets.
- A repeated pause is idempotent and retains the original actor and timestamp.

### Resume workspace

- Requires workspace-owner permission.
- Clears only the workspace overlay and records a new audited version.
- Dispatches only roles that are enabled, locally runnable, ready, and under
  budget.
- Never clears a manual, budget, readiness, or system pause on a role.

### Pause role while the workspace is paused

- Saves a local role hold beneath the overlay.
- The role remains paused when the workspace resumes.

### Resume or turn on a role while the workspace is paused

- Saves the user's desired local state.
- Does not dispatch work while the workspace overlay remains active.
- The UI says that the role will resume after the workspace is resumed.

## Multi-user concurrency

Workspace and role controls have separate monotonic versions. Mutations carry
the version the user viewed. A stale command that would change current state is
rejected with `409 Conflict` and the latest actor/state, while same-target
retries are idempotent. This prevents one browser from silently overwriting a
newer decision made in another browser.

The workspace row is locked for each workspace transition. Autonomous work is
fenced at task admission and again before paid or externally visible actions.
Queued work also carries the workspace control version so a task dispatched
before a pause cannot begin after the control changes.

## UI language

- Global surfaces say **Workspace agent paused** and identify who paused it
  and when.
- Global actions say **Pause workspace** and **Resume workspace**.
- A role held only by the workspace is shown as **On · held** and explains
  that it will resume automatically.
- A locally paused role under the workspace hold shows its saved pause actor,
  reason, and time and explains that it will remain paused afterward.
- Non-owners can see the state and attribution but are told that workspace
  controls are owner-only.

## Intake and in-flight work

A workspace pause blocks new autonomous scoring, assessments, auto-rejection,
agent tools, paid parsing, and Taali-native applications. Provider-hosted ATS
intake remains controlled by that provider, but Taali does not start paid
processing while paused. An already-started irreversible provider request
cannot be recalled; the runtime stops at the next control boundary.
