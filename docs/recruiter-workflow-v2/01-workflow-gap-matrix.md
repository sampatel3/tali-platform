# Recruiter Workflow V2 - Workflow Gap Matrix

Date: 2026-03-05
Owner: Engineering
Status: Spec locked for implementation baseline

## Scope
Compare current TAALI recruiter flow vs target Jobs-first V2 flow.

| Area | Current State | Target State (V2) | Gap | Priority |
|---|---|---|---|---|
| Primary entry | Recruiters start from Dashboard/Assessments inbox and context-switch to Candidates. | Recruiters start from `/jobs` with role-level stage counts and direct pipeline open. | Missing Jobs-first shell and route default. | P0 |
| Role pipeline navigation | Role candidates shown in table-first views, limited stage ergonomics. | `/jobs/:roleId` split-pane with stage tabs and consistent action bar. | Missing dedicated pipeline route + tabs + stage actions. | P0 |
| Stage model | `candidate_applications.status` mixed legacy semantics. | Canonical `pipeline_stage` + separate `application_outcome`. | Missing normalized transitions and compatibility mirror. | P0 |
| Transition governance | Stage movement not centrally guarded; mixed writes from routes/services. | Guarded transition service with actor policies + optimistic concurrency + idempotency keys. | Missing workflow engine and enforcement. | P0 |
| Event history | Partial audit trail through status updates only. | Append-only `candidate_application_events` with stage/outcome events and actor metadata. | Missing immutable event stream endpoint and storage. | P0 |
| Workable sync placement | Integration mostly visible in settings; stage semantics blur with local status. | Local stage/outcome stays canonical while TAALI writes invite, reject, and reopen actions back to Workable when explicitly configured. | Missing clearer write-back affordances, external drift indicator, and sync-state exposure in recruiter views. | P1 |
| Candidate detail density | Analytics-first, tab-heavy detail view. | Core-first right pane (identity/timeline/actions), advanced analysis collapsible. | Missing split-pane candidate workspace reuse across Jobs and Candidates. | P1 |
| Global candidates directory | Existing `/candidates` role-coupled interactions. | Global app directory with shared pane and filters by role/stage/outcome. | Missing unified directory endpoint + V2 page behavior. | P1 |
| Rollout controls | No org-level switch for this workflow. | Per-org `recruiter_workflow_v2_enabled` + global force-off. | Missing release guardrails and kill switches. | P0 |

## Exit Criteria Mapping
- P0 gaps addressed before default-on.
- P1 gaps included in V2 cutover scope.
- P2 polish deferred until post-cutover iteration.
