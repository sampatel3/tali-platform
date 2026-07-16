# Multi-user job and agent controls

Status: ready for merge through the current `main` release
Migration: `172_workspace_agent_control`
Last updated: 2026-07-16

## Outcome

The platform now treats each job as shared, versioned state instead of a
last-write-wins document. The controls are designed around five invariants:

1. A user must belong to the job's organization before any job data is exposed.
2. Organization membership alone does not grant job mutation rights.
3. A stale editor cannot silently overwrite a newer job or agent configuration.
4. Turning an agent off or pausing it prevents later actions from an already
   running cycle, even if the model request was in flight when the control changed.
5. Every material job/agent configuration change advances the job revision and
   creates an actor-attributed, append-only audit event in the same database
   transaction.

## Authorization model

The organization owner remains the break-glass administrator. Other users are
authorized from their `job_hiring_team` membership for the specific job.

| User relationship | View job | Edit job/spec | Control agent | Manage hiring team | Delete empty job |
| --- | ---: | ---: | ---: | ---: | ---: |
| Organization owner | Yes | Yes | Yes | Yes | Yes |
| Job hiring manager | Yes | Yes | Yes | Yes | Yes |
| Job recruiter | Yes | Yes | Yes | No | No |
| Job interviewer | Yes | No | No | No | No |
| Job coordinator | Yes | No | No | No | No |
| Other member of the organization | Yes | No | No | No | No |
| User from another organization | No | No | No | No | No |

Additional policy decisions:

- A job with no hiring-team rows fails closed for ordinary members. Only an
  organization owner can repair its membership.
- New jobs assign their creator as a hiring manager. Publishing a new
  requisition does the same for the publisher. A sister job copies the source
  team, with the creator as fallback.
- The migration assigns every active organization owner as a hiring manager on
  each existing live job so the fail-closed policy does not orphan legacy jobs.
- Removing a team member and performing another job mutation serialize through
  the same job row lock. A removed recruiter cannot win a race after access is
  revoked.
- Organization-wide pause/resume, cross-role scoring, graph synchronization,
  and integration administration are owner-only. Per-job integration and paid
  processing stages require the job's agent-control permission.

The centralized permission classes are `VIEW`, `EDIT_ROLE`, `CONTROL_AGENT`,
`MANAGE_HIRING_TEAM`, and `DELETE_ROLE`. Mutation routes call the shared
authorization boundary rather than recreating role-name checks locally.

## Concurrency model

Every role has a monotonically increasing integer `version`, initially `1`.
An interactive editor sends the version it rendered as `expected_version`.
The server then performs the following sequence in one transaction:

1. Select the live, tenant-scoped role `FOR UPDATE`.
2. Recheck the actor's per-job permission while the lock is held.
3. Compare `expected_version` with the locked row's current version.
4. Apply the mutation only when they match.
5. Increment the version exactly once for a material shared-state change.
6. Insert the corresponding audit event.
7. Commit both changes together and return the new version.

Core version-aware commands include role PATCH, job-spec PUT/upload, job status,
client assignment, star state, agent pause/resume, job deletion, linked
requisition edits, and requisition republish. Related shared configuration such
as hiring-team membership,
criteria, screening questions, linked tasks, agent feedback/answers, draft-task
approval, chat tools, and ATS synchronization also locks and advances the same
role revision. This invalidates an editor snapshot even when the changed data
is stored in a related table.

Identical retries and other true no-ops do not consume a revision. Operations
that make a related-table change deliberately create an audit boundary even
when the generic role-column diff is empty.

### Conflict contract

A stale version returns HTTP `409` and does not change the role:

```json
{
  "detail": {
    "code": "ROLE_VERSION_CONFLICT",
    "message": "This job changed after you opened it. Review the latest version before saving your changes.",
    "current_version": 8,
    "current_role": {},
    "changed_by": {
      "user_id": 42,
      "name": "Recruiter name",
      "email": "recruiter@example.com",
      "changed_at": "2026-07-14T12:34:56+00:00"
    }
  }
}
```

`current_role` is scoped to the command and may contain the full current role
or only the fields needed to reconcile that control. Actor fields are nullable
for automated integrations or deleted accounts.

The job-spec editor preserves the user's unsaved text on conflict and requires
an explicit choice:

- **Load latest** discards the local draft and adopts the server version.
- **Keep my draft** retains the text but rebases it onto the latest version so
  the user can review and deliberately save again.

Smaller settings controls adopt the latest server state, show a conflict toast,
and require the user to retry. A conflict is never auto-resubmitted.

## Agent power semantics

Agent control is enforced at more than the HTTP button:

- Run-now rejects a disabled agent and requires `CONTROL_AGENT`.
- Autonomous queued worker entry points recheck that the role is live, enabled,
  and not paused before beginning work. A manually authorized candidate batch
  remains a separate command and follows its own cancel control.
- A cycle captures the role version at start and refreshes the role before each
  paid round.
- It refreshes again immediately after a provider response and before executing
  any tools, decisions, or actions.
- A disabled, paused, or version-changed role aborts the cycle with a durable
  reason instead of applying work against stale configuration.
- Agent chat tools, needs-input writebacks, CV-gap rejection, and decision
  approve/override flows recheck current job permissions at their actual write
  boundary rather than trusting authority captured when a request was queued.
- Asynchronous agent chat captures the accepted role version when the durable
  task is enqueued. Read-only conversation may still complete after an unrelated
  edit, but any later mutating tool must match that exact version under lock.
- Workable/Bullhorn deletion events lock the role, disable and pause the agent,
  cancel pending activation intent, advance the revision, and audit the
  lifecycle transition. A later restore deliberately remains off and paused so
  automation cannot silently restart.

An already in-flight model call cannot be un-billed after a user turns the
agent off. The post-response guard ensures its output is not acted on. This is
the intended hard boundary: prevent effects after shutdown, while accurately
metering work that had already reached the provider.

## Audit history

`role_change_events` records:

- organization and role identifiers;
- nullable actor user ID;
- action name;
- `from_version` and `to_version`;
- bounded before/after changes;
- optional reason and request/correlation ID;
- server timestamp.

The row is inserted in the same transaction as the change. PostgreSQL enforces
append-only behavior with a trigger. The only permitted update is the foreign
key's exact actor anonymization from a user ID to `NULL` when an account is
deleted. Role deletion does not delete its history; owners can retrieve retained
history for the deleted numeric role ID.

The generic audit stream uses an explicit field allowlist. Full job descriptions,
job-spec text, signed file URLs, assessment packs, and similar large/sensitive
content are stored as SHA-256 fingerprints and lengths, not copied into audit
JSON. Values and collections are depth-, width-, and size-bounded.

Audit history is available newest-first from:

```text
GET /roles/{role_id}/change-events?limit=50&before_id={cursor}
```

Members can view history for a live job in their organization. Only an owner can
recover history after the role itself has been hard deleted.

## Protected mutation surfaces

The implementation covers the shared write paths that can affect a job or its
agent:

- role details, job spec, status, client, star state, criteria, interview focus,
  and linked assessment tasks;
- agent enable/disable, settings, thresholds, budgets, pause/resume, run-now,
  decisions, re-evaluation, and bulk controls;
- hiring-team add/remove;
- requisition publish, linked-brief edit, and republish;
- sister-role creation;
- agent-chat tools and draft approve/revise;
- recruiter-answer writebacks and CV-gap rejection;
- Workable and Bullhorn role synchronization and integration control routes.

Candidate workflow writes are authorized through the same job boundary without
advancing `Role.version`, because the application is separate workflow state.
The protected set includes application/sourced-candidate creation, application
updates and manual decisions, stage/outcome changes, interview/transcript and
note writes, ATS move/enrichment operations, CV upload and scoring, role-level
score/fetch/pre-screen/process batches, and assessment creation/retakes. Paid
or bulk paths require `CONTROL_AGENT`; ordinary recruiter workflow writes use
`EDIT_ROLE`. Public applicant-token routes remain outside this user/team policy.

Automated integration writes use a null/system actor plus an integration-specific
reason or request ID. Repeating an identical sync does not create audit noise.

A published requisition remains an intentional editing surface for its linked
job and can be re-published. Every authenticated linked-brief mutation requires
`EDIT_ROLE` plus the caller's `expected_version`; the server locks `Role` then
`RoleBrief`, compares the revision, applies the actual brief change, increments
`Role.version` once, and appends its audit event in the same transaction. Slow
LLM-backed chat/intake/drafting calls run against a detached working copy and
repeat authorization plus revision comparison at the commit boundary, so the
Role lock is not held across provider latency. Client intake still closes on
linking because that public token flow cannot participate in the authenticated
job-team revision protocol.

## Schema and deployment plan

### Phase 1 — pre-deployment checks

1. Confirm there is one Alembic head and migration `166` follows
   `165_score_job_authority`.
2. Back up the production database and record current row counts for `roles`,
   `job_hiring_team`, and live roles without an owner.
3. Confirm every active organization has at least one owner. Repair organizations
   without an owner before enabling fail-closed job mutation policy.
4. Ensure web and worker deployments are built from the same revision. Old
   workers do not know the mid-cycle abort rule.

### Phase 2 — additive migration

Run `python -m alembic upgrade head` before starting the new application code.
The migration:

1. Adds `roles.version NOT NULL DEFAULT 1`.
2. Changes hiring-team foreign keys to cascade on organization, role, or user
   deletion.
3. Backfills organization owners as hiring managers on every live legacy role.
4. Creates and indexes `role_change_events`.
5. Installs the PostgreSQL append-only trigger.

The application change and frontend should be deployed as one coordinated
release because mutation schemas now require an editor version. Cached old
browser bundles may receive a validation error and should be refreshed; do not
silently fall back to last-write-wins behavior.

### Phase 3 — coordinated application rollout

1. Deploy web/API instances.
2. Deploy all general, scoring, and scheduled workers from the same revision.
3. Deploy the frontend and invalidate the prior static bundle at the CDN.
4. Keep automatic agent sweeps paused during the short mixed-worker window if
   the deployment platform cannot replace workers atomically.
5. Resume sweeps only after health checks show all worker pools on the new build.

### Phase 4 — production smoke matrix

Use two ordinary test users plus one owner in the same organization:

1. Assign user A as recruiter; leave user B unassigned.
2. Verify A can edit and B receives `403` for the same mutation.
3. Open the same job in two A/owner browser sessions. Save in one, then verify
   the other receives `409` and retains its job-spec draft.
4. Turn the agent on, start a run, then turn it off from the other session.
   Verify no subsequent action is applied and the run records an abort reason.
5. Remove A from the hiring team and verify the next mutation fails.
6. Confirm each material step advances the version once and appears in the
   change-events endpoint with the correct actor.
7. Trigger an identical Workable/Bullhorn re-sync and confirm it creates no
   additional version or audit event.
8. Verify a cross-organization user receives no role or audit data.

### Phase 5 — monitoring

For the first release window, monitor:

- count and rate of `ROLE_VERSION_CONFLICT` responses;
- `403` rates grouped by permission class and endpoint;
- roles with no hiring-team rows;
- role version increments with no corresponding audit row;
- audit insert/trigger failures;
- cycles aborted for disabled, paused, or configuration-changed roles;
- queued agent tasks that exit because the agent is disabled;
- worker versions during deployment;
- audit-table growth and index size.

Alert on any committed version without a matching `to_version`, because the
change and audit insert are intended to be atomic. Conflict responses are an
expected collaboration signal; a sudden sustained increase is a UX or client
refresh problem, not necessarily a backend failure.

### Rollback policy

Prefer a forward fix after migration `166` has accepted production writes.
Rolling application code back leaves additive columns and audit rows harmless,
but old code would bypass the new permission/version boundaries. If an emergency
application rollback is required, pause agent workers and restrict shared job
editing until the fixed release is restored.

Do not run the migration downgrade after audit events exist unless loss of the
new audit history is explicitly approved. The downgrade drops the audit table
and therefore is not the normal operational rollback.

## Verification gates

The release is ready only when all of these gates pass:

- authorization matrix tests, including unassigned/interviewer/coordinator and
  cross-tenant denials;
- stale-write, no-op, row-lock, and audit atomicity tests;
- role/job-spec/hiring-team/requisition lifecycle regressions;
- agent route, agent chat, needs-input, CV-gap, and mid-cycle shutdown tests;
- Workable and Bullhorn concurrency/no-op audit tests;
- focused frontend conflict tests;
- frontend typecheck, production build, and architecture guard;
- Python compilation, Alembic single-head check, and `git diff --check`.

Current worktree result (2026-07-14):

- 553 backend tests passed across non-overlapping core-control, agent/runtime,
  candidate/application, feedback-note, and architecture suites; no failures;
- all 92 frontend test files passed (699 passed, 3 skipped), followed by a
  clean typecheck, production build, frontend architecture guard, and motion
  guard;
- Python compilation and all 13 backend architecture gates passed;
- Alembic reports the single `169_role_collaboration_controls` head, and static
  PostgreSQL SQL generation for `165 -> 166` passed;
- `git diff --check` passed.

The remaining release validation is the production backup/preflight, migration,
mixed-worker rollout control, and smoke matrix above; no production deployment
was performed from this worktree.

## Known limits and follow-ups

The job/agent configuration boundary described above is enforced now. The
following candidate-workflow hardening remains explicit rather than being
hidden behind a broader claim:

- an endpoint-by-endpoint candidate-workflow matrix deciding which interview,
  note, outcome, assessment, and external-ATS actions interviewers and
  coordinators may perform; ambiguous routes currently fail closed behind
  recruiter/hiring-manager job permissions;
- durable actor/version envelopes for every manually launched candidate batch,
  with authority rechecked before each paid or externally visible unit; the
  unified process worker already rechecks its initiating user at entry, while
  older batch/fetch/pre-screen workers are authorized at dispatch;
- two-phase preparation for remaining candidate mutations that call external
  providers while holding the Role authorization lock, reducing Turn-off and
  membership-revocation latency without weakening commit ordering;

Product and operational follow-ups:

- a first-class audit timeline in the job UI (the API is implemented now);
- full job-spec revision storage and a three-way merge editor instead of
  draft/reload conflict resolution;
- real-time change notifications so an open page can refresh before save;
- a separately configurable permission for recruiters who may edit a job but
  must not control its agent;
- retention/export policy for the append-only audit table;
- metrics dashboards and alert thresholds based on production traffic.
