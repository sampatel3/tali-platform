# RALPH_TASK.md — TALI Platform Comprehensive Audit & Execution Plan (2026-02-13)

> **STATUS: ACTIVE (REOPENED)**
> This document replaces the previously archived launch checklist. It is now the source of truth for the next execution cycle.

---
task: TALI Platform - Codebase + Product Hardening Sprint
test_command: "cd backend && pytest -q && cd ../frontend && npm test -- --run"
---

## 0) Parallel lane assignment (4-agent split, non-overlapping)

- **Lane A (Platform/Backend reliability):** schema correctness, request tracing, health readiness, task correlation.
- **Lane B (CI & test gates):** production smoke isolation, CI matrix, coverage/lint quality gates.
- **Lane C (Frontend quality):** test suite stabilization, jsdom/test env hardening, warning reduction pass.
- **Lane D (Product UX/workflow):** recruiter insights, Workable/report actions, candidate document visibility/download UX.

Execution rule: each lane only touches its scoped files to avoid overlap, then merges sequentially.


## 0.1) Immediate production-parity hotfix plan (2026-02-13 priority)

Context from live user testing revealed critical workflow regressions that must be handled before additional feature work.

- [x] **Remove candidate-side CV gate on assessment start**
  - Candidate should be able to start directly from invite link.
  - CV upload remains a recruiter/candidate profile artifact, not a runtime gate.
  - Backend start endpoint no longer hard-fails on missing CV.

- [x] **Remove dashboard-side assessment creation entry point**
  - Source of truth for creating/sending assessments is the Candidates page.
  - Dashboard is assessment monitoring/reporting only.

- [x] **Restore assessment runtime context visibility (core product path)**
  - Show task context (description/scenario) in the assessment workspace.
  - Show repository context/file list and file contents when repo structure is provided.
  - Extend assessment start payload to include `task_key`, `role`, `scenario`, `repo_structure`, `evaluation_rubric`, `extra_data`.

- [ ] **Follow-up hardening (next slice)**
  - Validate all task creation/import paths persist `scenario` and `repo_structure` end-to-end.
  - Add explicit UX fallback copy when repo context is missing on a task.
  - Add focused E2E for “History Backfill” (task context visible + repo files visible before first prompt).

## 1) Executive summary

TALI is **substantially built** and demonstrates a credible end-to-end product:

- Multi-tenant auth, candidate/task/assessment lifecycles, AI-assisted assessment flow, scoring pipeline, billing/integration scaffolding, and broad automated backend test coverage are present.
- Frontend ships a full recruiter + candidate experience, but has notable maintainability and test fragility concerns.

However, this audit identified **critical execution gaps** that must be addressed before relying on this repo as “production-stable”:

1. **Test suite signal quality is degraded**:
   - Backend run produced **27 failures** (26 external production-smoke proxy failures + 1 real schema validation mismatch).
   - Frontend run produced **29 failures** concentrated in dashboard/candidates/tasks tests.
2. **Documentation drift** between README, historical task files, and current implementation status.
3. **Monolithic frontend architecture** (`App.jsx`) increases regression risk and slows feature work.
4. **Scoring/product UX gaps** still exist around explainability, reliability, and recruiter actions.

This plan is structured so Cursor/Claude Code can execute in controlled phases.

---

## 2) Scope of this plan

- Comprehensive codebase review (backend + frontend + tests + docs)
- Product readiness review (UX, data quality, ops reliability)
- Concrete, prioritized engineering tasks with acceptance criteria
- “Definition of done” that can be validated via CI/local checks

---

## 3) Audit findings

## 3.1 What is strong today

- Strong backend functional surface area and broad test corpus.
- Security and platform hardening features are present (headers, CORS controls, auth/rate-limit paths).
- Candidate assessment workflow appears complete including Claude + E2B interaction and analytics capture.
- Frontend includes core business screens and candidate detail visualizations.

## 3.2 Critical issues (must fix first)

### A. Backend test reliability + correctness

- [x] **Fix schema validation mismatch**:
  - `AssessmentCreate.candidate_name` currently accepts empty string; unit test expects validation failure.
  - Add `min_length=1` (or pre-validator to normalize blank-to-None) and align tests accordingly.

- [x] **Quarantine true production smoke tests from local default run**:
  - `tests/test_qa_production_smoke.py` depends on live URL and failed in this environment due to proxy restrictions.
  - Mark as `@pytest.mark.production` (or equivalent) and exclude by default in local/CI baseline.

### B. Frontend test suite breakage

- [x] **Fix 29 failing frontend tests**:
  - Failing files: `Dashboard.test.jsx`, `Candidates.test.jsx`, `Tasks.test.jsx`.
  - Root causes likely route/render assumptions and async waiting patterns after UI refactors.
  - Update tests to current UX contracts (headings, navigation state, responsive rendering behavior).

- [ ] **Address noisy act()/jsdom warnings**:
  - Wrap async state updates in `act`-safe interactions.
  - Mock/patch unsupported browser APIs (`scrollTo`, navigation) in test setup.

### C. Documentation and planning drift

- [x] **Reconcile README with current implementation**:
  - Remove stale statements (e.g., missing test scripts when scripts now exist).
  - Align feature-status sections with actual backend/frontend behavior.

- [x] **Cross-link plan docs**:
  - Keep `PRODUCT_PLAN.md` and `RALPH_TASK.md` non-contradictory.
  - Archive completed sections with timestamped changelog entries.

---

## 3.3 High-priority product/code improvements

### D. Frontend architecture refactor (risk reduction)

- [ ] Break `frontend/src/App.jsx` into feature modules:
  - `pages/` (Dashboard, Candidates, Tasks, CandidateDetail, Settings, Landing)
  - `components/` shared UI
  - `hooks/` data + state orchestration
  - `utils/formatters` and scoring adapters
- [ ] Introduce route-level composition (keep hash routing if desired, but isolate page boundaries).
- [ ] Add typed API response adapters (even in JS) to prevent snake_case/camelCase drift.

### E. Scoring product quality

- [ ] Verify full scoring payload consistency from backend to frontend visualizations.
- [ ] Add explicit fallback UX for partially available scores (e.g., Claude timeout, fit-score unavailable).
- [x] Improve recruiter interpretability:
  - Top 3 strengths
  - Top 3 risks
  - Recommended interview focus questions based on observed behavior

### F. Recruiter workflow completeness

- [x] Confirm/ship end-to-end “Post to Workable” action in recruiter UI + backend endpoint behavior.
- [x] Confirm/ship report export (PDF/JSON summary) from candidate detail.
- [x] Ensure candidate document visibility/download links are robust.

---

## 3.4 Operational hardening

### G. Observability and runtime confidence

- [x] Add structured request IDs correlated across API logs + Celery tasks.
- [x] Add explicit health checks for external dependencies (Claude/E2B optional status indicators).
- [x] Add error budget dashboard metrics:
  - assessment start failures
  - Claude request failures
  - sandbox provisioning latency
  - scoring computation latency

### H. CI and quality gates

- [x] Create/upgrade CI pipeline with separate jobs:
  - backend unit/integration (default)
  - frontend unit (default)
  - production smoke (manual/scheduled only)
- [x] Enforce minimum coverage thresholds for touched files or critical modules.
- [x] Add lint/format checks if absent.

---

## 4) Execution plan (Cursor-ready)

## Phase 0 — Stabilize test signal (Day 1)

- [x] Patch `AssessmentCreate` validation and corresponding schema tests.
- [x] Mark production smoke tests with dedicated marker and default exclusion.
- [x] Fix failing frontend tests to green baseline.

**Exit criteria:**
- [x] `cd backend && pytest -q -m "not production"` passes.
- [x] `cd frontend && npm test -- --run` passes.

## Phase 1 — Docs + status truthfulness (Day 1-2)

- [x] Rewrite README “Implemented / Not yet implemented” based on actual code.
- [x] Add a short “Known limitations” section for external dependency behaviors.
- [x] Keep `PRODUCT_PLAN.md` and `RALPH_TASK.md` aligned with clear ownership.

**Exit criteria:**
- [x] No contradictory feature-status claims across key docs.

## Phase 2 — Frontend decomposition (Day 2-4)

- [x] Extract CandidateDetailPage from `App.jsx` first (highest complexity).
- [ ] Extract Dashboard/Candidates/Tasks into separate modules.
  - [x] Dashboard page extracted to `frontend/src/pages/DashboardPage.jsx`.
  - [x] Candidates page extracted to `frontend/src/pages/CandidatesPage.jsx`.
  - [ ] Tasks page extraction pending.
- [ ] Introduce minimal page-level tests for each extracted module.

**Exit criteria:**
- [ ] `App.jsx` reduced to routing/composition shell.
- [ ] Existing behavior parity maintained.

## Phase 3 — Product polish and recruiter value (Day 4-6)

- [x] Ship actionable score insights (strengths/risks/interview prompts).
- [x] Complete Workable post action + visible status trail.
- [x] Implement report export from candidate detail.

**Exit criteria:**
- [x] Recruiter can complete full evaluation loop without manual data copy.

## Phase 4 — Ops + CI confidence (Day 6-7)

- [x] Add CI matrix with clear pass/fail gates.
- [x] Add production-smoke scheduled/manual workflow.
- [x] Add observability dashboard definitions to docs.

**Exit criteria:**
- [x] New PRs cannot merge with broken baseline tests.

---

## 5) Backlog (non-blocking)

- [ ] TypeScript migration (incremental, page-by-page).
- [ ] React Router migration away from hash routing.
- [ ] SSO/SAML and enterprise access controls.
- [ ] White-label branding controls.
- [ ] Candidate comparison and cohort analytics views.

---

## 6) QA commands

Use these exact commands during implementation:

```bash
# Backend fast path (local-safe)
cd backend && pytest -q -m "not production"

# Backend full (includes production smoke; expected to require live env)
cd backend && pytest -q

# Frontend tests
cd frontend && npm test -- --run

# Frontend production build
cd frontend && npm run build
```

---

## 7) Definition of done for this reopened RALPH cycle

- [x] Backend and frontend default test suites pass consistently.
- [x] Production-only tests are explicitly separated from local baseline.
- [x] README/task plans reflect actual product truth.
- [ ] Frontend architecture no longer concentrated in a single mega-file.
- [x] Recruiter-facing evaluation workflow is complete and exportable.
- [x] CI enforces these guarantees automatically.

