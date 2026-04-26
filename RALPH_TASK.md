# RALPH_TASK.md — Workable-Aligned Recruiter Workflow Redesign

## Source of Truth
This file is the single source of truth for implementing the Workable-aligned recruiter and TAALI workflow redesign.

## Project Goal
Ship a Jobs-first recruiter workflow and candidate pipeline UX that feels operationally close to Workable while keeping TAALI scope focused (not full ATS), and preserving future Workable service integration compatibility.

## Key Constraints
- Keep Workable scope bounded: support invite/reject/reopen write-back only, not full ATS parity.
- Preserve backward compatibility for existing role/application records.
- Use feature-flag rollout with safe fallback to legacy flows.
- Do not replicate full Workable end-to-end recruitment feature set.
- Maintain TAALI brand/system styling (pattern parity, not pixel clone).

## Locked Assumptions
- Primary IA: Jobs-first navigation model.
- Pipeline taxonomy: TAALI core stages.
- Candidate detail scope: Core-first with advanced details in secondary panels.
- Integration depth: Read sync plus targeted invite/reject/reopen write-back now; broader ATS behavior later.
- Timeline target: 10–12 weeks to full polish.

## Major File Paths In Scope
- Backend
  - `/Users/sampatel/tali-platform/backend/app/domains/assessments_runtime/applications_routes.py`
  - `/Users/sampatel/tali-platform/backend/app/domains/assessments_runtime/roles_management_routes.py`
  - `/Users/sampatel/tali-platform/backend/app/domains/assessments_runtime/role_support.py`
  - `/Users/sampatel/tali-platform/backend/app/domains/workable_sync/routes.py`
  - `/Users/sampatel/tali-platform/backend/app/components/integrations/workable/sync_service.py`
- Frontend
  - `/Users/sampatel/tali-platform/frontend/src/App.jsx`
  - `/Users/sampatel/tali-platform/frontend/src/features/dashboard/DashboardNav.jsx`
  - `/Users/sampatel/tali-platform/frontend/src/features/candidates/CandidatesPage.jsx`
  - `/Users/sampatel/tali-platform/frontend/src/features/candidates/CandidatesTable.jsx`
  - `/Users/sampatel/tali-platform/frontend/src/features/candidates/CandidateDetailPage.jsx`
  - `/Users/sampatel/tali-platform/frontend/src/shared/api/rolesClient.js`
  - `/Users/sampatel/tali-platform/frontend/src/shared/api/assessmentsClient.js`

## Status Legend
- `[ ]` todo
- `[~]` in progress
- `[x]` done

## Agent Ownership
- Agent 0: Orchestrator / Integration Lead
- Agent 1: Workflow Audit + Spec Freeze
- Agent 2: Backend Domain + Pipeline APIs
- Agent 3: Frontend IA + Jobs Hub
- Agent 4: Candidate Workspace + Stage Operations
- Agent 5: Workable Integration Contract
- Agent 6: QA, Analytics, Rollout, Docs

## Phase 1 — Discovery + Scope Freeze
### Before You Start
- Baseline branches/worktrees are created for all agents.
- Current-state recruiter workflow is captured from existing app.
- Reference patterns from Workable screenshots are documented.

- [ ] Agent 1: Produce canonical workflow map for Jobs Hub, Job Pipeline, Candidate Workspace, Candidate Detail.
- [ ] Agent 1: Define in-scope vs out-of-scope feature list for MVP redesign (explicitly exclude full ATS behaviors). `[HUMAN REVIEW]`
- [ ] Agent 1: Define measurable recruiter success criteria (speed to shortlist, stage movement friction, search/filter efficiency).
- [ ] Agent 0: Publish interface contract template for API/type/event changes.
- [ ] Agent 6: Capture baseline telemetry event list and baseline UX KPI snapshot from current experience.

## Phase 2 — Backend Contract & Data Model Alignment
### Before You Start
- Phase 1 workflow spec is approved.
- Stage taxonomy is finalized as TAALI canonical stages.
- Backward compatibility rules for existing applications are documented.

- [ ] Agent 2: Add canonical pipeline stage enum and mapping helpers in backend domain layer.
- [ ] Agent 2: Add/normalize recruiter API for listing jobs with per-stage candidate counts.
- [ ] Agent 2: Add/normalize recruiter API for listing candidates by role/stage/status with pagination.
- [ ] Agent 2: Add/normalize recruiter API endpoint for stage transition actions with transition validation.
- [ ] Agent 2: Add backend tests for valid transitions, invalid transitions, filter behavior, and pagination.
- [ ] Agent 5: Define Workable-to-TAALI stage/status mapping contract for bounded read sync plus invite/reject/reopen write-back.
- [ ] Agent 5: Align Workable sync DTO/event payloads to canonical internal stage contract.
- [ ] Agent 0: Review Agent 2 + Agent 5 interface outputs for drift and sign off contract freeze. `[HUMAN REVIEW]`

## Phase 3 — IA + Jobs-Centric Shell
### Before You Start
- Phase 2 APIs are merged or mocked with frozen schema.
- Primary nav model is approved as Jobs-first.
- UI pattern guidelines for Workable parity are documented.

- [ ] Agent 3: Update app shell navigation hierarchy to Jobs-first with Candidates as second primary surface.
- [ ] Agent 3: Implement Jobs Hub page/list with search/filter and role cards showing stage counts.
- [ ] Agent 3: Add quick role actions in Jobs Hub (open pipeline, add candidate, publish state badge).
- [ ] Agent 3: Refactor route composition so Jobs Hub and Candidate Workspace are first-class routes.
- [ ] Agent 6: Instrument analytics events for Jobs Hub search/filter/open-role actions.
- [ ] Agent 3: Add frontend tests for Jobs Hub render, filter/search behavior, and stage-count consistency.

## Phase 4 — Candidate Workspace + Pipeline Interaction
### Before You Start
- Jobs Hub route exists and can open role context.
- Stage transition API is stable.
- Candidate query payload contract is performance-validated.

- [ ] Agent 4: Build split candidate workspace layout (candidate queue left, profile/context panel right).
- [ ] Agent 4: Implement stage move controls for single and batch candidate actions.
- [ ] Agent 4: Implement shortlist/disqualify and recruiter productivity actions aligned to stage model.
- [ ] Agent 4: Integrate workspace with canonical API clients (`rolesClient.js`, `assessmentsClient.js`).
- [ ] Agent 4: Add frontend tests for workspace interactions and list/detail state coherence.
- [ ] Agent 3: Align shared UI patterns/components between Jobs Hub and Candidate Workspace.
- [ ] Agent 0: Resolve ownership conflicts on shared components and freeze component boundaries. `[HUMAN REVIEW]`

## Phase 5 — Workable Integration Readiness
### Before You Start
- Canonical stages are in backend and frontend.
- Controlled Workable write-back boundaries are approved.
- Stage mapping matrix is reviewed by product + engineering.

- [ ] Agent 5: Implement adapter layer for forward-compatible TAALI↔Workable stage mapping plus invite/reject/reopen write-back (no full ATS orchestration).
- [ ] Agent 5: Add transform tests for known mapped states, unknown states, and terminal state behavior.
- [ ] Agent 5: Document field-level mapping (internal canonical fields vs Workable payload fields).
- [ ] Agent 2: Add backend guards to preserve canonical internal state when external payload is missing/malformed.
- [ ] Agent 6: Add observability checks for sync status, stale payloads, and mapping failures.

## Phase 6 — QA, Rollout, Documentation, Handoff
### Before You Start
- Phases 2–5 are merged into integration branch.
- Feature flags are wired for safe enable/disable.
- Telemetry is verified in staging.

- [ ] Agent 6: Execute unit/API/UI/E2E test matrix for all redesigned recruiter journeys.
- [ ] Agent 6: Run regression validation for feature-flag OFF fallback behavior.
- [ ] Agent 6: Produce phased rollout playbook with rollback triggers and monitoring checks.
- [ ] Agent 6: Produce post-launch KPI dashboard definition and review cadence.
- [ ] Agent 1: Validate implemented workflows against Phase 1 acceptance criteria. `[HUMAN REVIEW]`
- [ ] Agent 0: Final release candidate signoff with unresolved risk register. `[HUMAN REVIEW]`

## Cross-Phase Rules
- [ ] Any contract-changing task must include updated API/type docs in the same PR.
- [ ] Any workflow UI task must include analytics events and UI test coverage.
- [ ] Any Workable sync or write-back change must prove bounded ATS safety via tests.
- [ ] Any phase completion requires explicit gate check against its "Before You Start" preconditions.

## Completion Criteria
- [ ] Jobs-first recruiter workflow is fully usable behind a feature flag.
- [ ] Candidate stage movement and filtering workflows are operational and tested.
- [ ] Workable-compatible adapter contract is implemented and documented for read sync plus invite/reject/reopen write-back.
- [ ] QA and rollout package is complete with signoffs from Agent 0, Agent 1, and Agent 6.
