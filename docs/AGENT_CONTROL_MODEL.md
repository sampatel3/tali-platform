# Agent control model

Taali stores agent control on each role. A role can be off, paused, or enabled
and ready to run. The Home and Jobs global controls are workspace-owner-only
bulk operations over those role controls; they are not a separate workspace
execution overlay.

The API retains workspace-named endpoints and a shared control version for
compatibility and concurrency. Those names do not mean that a current bulk
Pause creates a hidden organization-wide hold.

## Role states

| Role state | Runs autonomously | Changed by bulk Pause | Considered by bulk Resume |
| --- | --- | --- | --- |
| Off | No | No | No |
| Enabled and running | Yes | Paused | No |
| Enabled and already paused | No | No | Yes |

An existing role pause can be recruiter-authored or produced by a runtime
safety guard. Bulk Pause leaves its timestamp, reason, actor, version, and
history unchanged. This preserves the distinction between a pause created by
the bulk action and a hold that was already protecting the role.

## Commands

### Pause running agents

- Requires workspace-owner permission.
- Pauses each enabled, currently unpaused role in one serialized bulk action.
- Leaves disabled roles and already-paused roles untouched. It does not erase
  or relabel individual, budget, readiness, bootstrap, or system holds.
- Keeps pending review decisions and configured role budgets intact because it
  soft-pauses the role rather than turning agent mode off.
- Records the bulk actor and action, and records a role change for every role
  whose state changed.
- Does not create a workspace execution overlay. Each affected role can be
  resumed independently from its role page.

### Resume eligible paused agents

- Requires workspace-owner permission.
- Explicitly attempts to resume every enabled, paused role, regardless of
  whether that pause came from an earlier bulk action, an individual action,
  or a safety guard.
- Clears a role's pause only when its monthly budget and complete runtime
  readiness checks are healthy. Ineligible roles remain paused and are
  reported as skipped so the UI can direct the owner to the role that needs
  attention.
- Attempts an immediate agent cycle for each role it successfully unpauses.
  A dispatch failure is reported for attention rather than silently presented
  as a fully successful bulk resume.
- Leaves agent-off roles off.

Bulk Resume is therefore a deliberate request to resume eligible role agents,
not the removal of a temporary workspace layer. Owners who want to preserve a
particular manual pause should resume only the intended roles individually.

## Multi-user concurrency and audit

Global bulk commands carry the shared control version the user viewed. The
organization row and target role rows are locked in a consistent order while
the command is applied. A stale command that would conflict with a newer bulk
action is rejected with `409 Conflict` and the latest action context; a safe
same-target retry can remain idempotent.

Each changed role receives its own monotonic version and append-only role
change event. A real bulk transition also advances the shared control version
and appends a workspace-level bulk-action event. A Resume attempt for which
every role fails the safety checks does not claim a state transition or replace
the latest successful actor.

## UI language

- Global actions say **Pause running agents** and **Resume eligible paused
  agents**.
- Aggregate status reports how many roles are running and how many are paused;
  it does not present current bulk pauses as a workspace overlay.
- A partial Resume reports both the resumed and skipped counts and points the
  owner to budget/readiness status.
- Non-owners can see aggregate agent state, but the bulk buttons explain that
  only workspace owners can use them.

## Intake and in-flight work

A role pause blocks new autonomous work for that role at the normal admission
and side-effect boundaries, including agent cycles and guarded paid actions.
It does not remove pending decisions or change provider-owned ATS intake.
Already-started irreversible provider requests cannot be recalled; execution
stops at the next applicable role-control boundary.

## Legacy overlay compatibility

Older releases could persist an organization-wide pause. Compatibility readers
remain temporarily so rolling deployments fail safely, and the database
migration converts a pre-existing legacy hold into role pauses. New global
Pause and Resume commands must not create or depend on that legacy overlay.
