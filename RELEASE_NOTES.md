# Release Notes — Hardening Sprint

Date: 2026-02-13

## Shipped outcomes

### P0 — Pre-pilot role-first hardening reset
- Added irreversible migration `015_role_first_applications_pause_reset` for role-first workflow rollout.
- Migration deletes all rows from `assessments`, `candidate_applications`, and `candidates` in every environment by design.
- Release gate flag logged at migration time: `2026-02-13-role-first-hardening`.

### P1 — Core assessment integrity
- Ensured assessment runtime context includes `task_key`, `role`, `scenario`, `repo_structure`, rubric and extra data.
- Added/validated history-backfill context coverage so candidates see task + repository context before first prompt.
- Verified telemetry events for code execution and AI prompt interactions include timing/session metadata.
- Enforced paused-state lock for CV upload endpoints in addition to execute/submit/chat.

### P2 — Scoring completeness + glossary
- Aligned score categories/metrics and centralized glossary descriptions for scoring dimensions.
- Improved rendering fallback behavior when partial scoring payloads are present.

### P3 — Candidate comparison UX
- Added Candidate A/B comparison controls in candidate detail.
- Added radar overlay and side-by-side comparison modes.

### P4 — Frontend quality
- Default frontend tests pass in CI/local baseline.
- Remaining test warning cleanup tracked in task plan.

### P5 — Brand/landing
- Added explicit "What we test (30+ signals)" section and clarified value proposition on landing page.
- Centralized brand config for title/name/email pathways.

### P6 — Model strategy + cost observability
- Kept non-production default on lowest-cost Claude model tier with env override for production tier.
- Added billing cost observability endpoints and surfaced threshold health in dashboard settings.

### P7 — Integration + release gate
- Ran backend local-safe suite, frontend unit tests, and production build successfully.
- Reconciled active docs with updated hardening plan and shipped outcomes.
- Added role integrity regression checks (role delete guard and task unlink guard).
- Added migration contract test for destructive reset guarantees.
