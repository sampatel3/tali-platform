# RALPH_TASK.md — TALI Platform Comprehensive Audit & Execution Plan (2026-02-13)

> **STATUS: ACTIVE (REOPENED)**
> This document replaces the previously archived launch checklist. It is now the source of truth for the next execution cycle.

> **Review refresh (2026-02-13):** status markers in this file were rechecked against the current codebase. Candidate Detail, Dashboard, and Candidates pages are extracted from `App.jsx`; Tasks extraction and broader decomposition remain pending.

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

## 2.1) Additional refinement directives (2026-02-13)

- [ ] **Candidate comparison UX v1**
  - Add a comparison mode in Candidate Detail that overlays one candidate on another in the radar chart.
  - Also provide side-by-side comparison cards/tables for category and metric-level scores.
  - Keep candidate pickers explicit (`Candidate A`, `Candidate B`) to avoid accidental comparisons.

- [ ] **Dimension definitions in plain English**
  - Every score dimension shown in charts must include an accessible description.
  - Add hover/tooltips on radar axes and bars; include a persistent glossary panel as fallback for touch devices.

- [ ] **Clarify product value on front page**
  - Expand landing page copy to clearly explain what is being tested (prompt clarity, debugging behavior, autonomy, fraud signals, communication quality, etc.).
  - Include a short “What we measure (30+ signals)” section with concrete examples.

- [ ] **Brand-agnostic readiness**
  - Remove hard-coded brand strings from reusable code paths and centralize display name/domain references in config/constants.
  - Ensure email templates, page titles, and UI logos can be swapped without broad code edits.

- [ ] **Core assessment integrity checklist**
  - (a) Verify all task context is visible in-IDE before first prompt.
  - (b) Verify ALL interaction telemetry is tracked and queryable.
  - (c) Verify scoring remains comprehensive across all categories/metrics with detailed explanations.
  - (d) Keep a basic CV↔Job Spec fit path (LLM-based baseline plus optional similarity-search fallback).
  - (e) Default testing/staging model to cheapest Claude tier; keep model configurable per environment.
  - (f) Add cost monitoring for Claude/E2B/email/storage with per-assessment and per-tenant rollups.
  - (g) Add weekly “unknown unknowns” review: scoring drift, prompt-injection handling, replay/debug tooling, and fairness checks.

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
- [x] Candidate Detail, Dashboard, and Candidates page extraction completed.
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

## 4) Multi-agent execution plan (assignment-ready)

This section restructures execution into assignable phases so multiple agents can run in parallel with clear handoffs.

### Agent roster (recommended)

- **Agent A — Core assessment runtime (backend + candidate runtime UX)**
- **Agent B — Scoring quality + explainability**
- **Agent C — Frontend architecture + comparison UX**
- **Agent D — Landing page + brand-agnostic refactor**
- **Agent E — Cost/model controls + observability**
- **Agent F — CI/QA + release management**

### Parallelization rules

- One agent owns one phase at a time; cross-phase edits require explicit handoff notes in PR description.
- Shared files (`frontend/src/App.jsx`, scoring schema contracts, shared API clients) require merge order: **B → C → F**.
- Every phase must include: scope, file boundaries, acceptance criteria, and validation commands.

### Phase P0 — Baseline lock & branch strategy (Owner: Agent F)

**Goal:** freeze baseline quality signal before new work.

- [ ] Confirm green baseline on default local-safe test commands.
- [ ] Confirm production-smoke is still isolated from default path.
- [ ] Create working branch plan (one branch per phase/agent).

**Validation:**
- `cd backend && pytest -q -m "not production"`
- `cd frontend && npm test -- --run`

---

### Phase P1 — Core assessment integrity (Owner: Agent A)

**Goal:** enforce the core product promise around task assessment reliability.

- [ ] Verify all task context is visible in IDE before first prompt (`task`, `scenario`, `repo_structure`, rubric context).
- [ ] Add/verify fallback UX when repo context is missing.
- [ ] Verify full telemetry capture for all candidate interactions (prompt/response/code/test/timing/session metadata).
- [ ] Add focused E2E for “History Backfill” context visibility.

**File scope (expected):**
- `backend/app/components/assessments/*`
- `backend/app/schemas/*assessment*`
- `frontend/src/components/assessment/*`
- `frontend/src/pages/*Candidate*` (only if needed for context display)

**Acceptance criteria:**
- Candidate sees complete task context pre-coding.
- No major telemetry gaps in stored assessment artifacts.

---

### Phase P2 — Scoring completeness + dimension glossary (Owner: Agent B)

**Goal:** make scoring deeply comprehensive and understandable in plain English.

- [ ] Verify category + metric coverage remains comprehensive and mapped frontend↔backend.
- [ ] Add/centralize plain-English descriptions for every scoring dimension.
- [ ] Ensure chart tooltip/hover content reads from one glossary source of truth.
- [ ] Add fallback display for missing/partial scoring components.

**File scope (expected):**
- `backend/app/components/scoring/*`
- `backend/app/components/assessments/repository.py`
- `frontend/src/pages/CandidateDetailPage.jsx`
- `frontend/src/components/**/*score*`
- `frontend/src/lib/*` (adapters/formatters)

**Acceptance criteria:**
- Each visible dimension has a clear explanation.
- Frontend safely handles partial scores without confusing empty states.

---

### Phase P3 — Candidate comparison UX (Owner: Agent C)

**Goal:** recruiter can compare Candidate A vs Candidate B quickly and accurately.

- [ ] Add candidate comparison mode entry point from candidate detail.
- [ ] Add radar overlay mode (A on top of B).
- [ ] Add side-by-side mode (cards/table with per-category deltas).
- [ ] Add explicit candidate selectors and clear “comparison active” state.

**File scope (expected):**
- `frontend/src/pages/CandidateDetailPage.jsx`
- `frontend/src/components/**/*radar*`
- `frontend/src/components/**/*comparison*`
- `frontend/src/lib/api.js` (if extra fetches needed)

**Acceptance criteria:**
- Overlay and side-by-side both functional.
- Recruiter can compare candidates without leaving candidate detail workflow.

---

### Phase P4 — Frontend decomposition completion (Owner: Agent C)

**Goal:** reduce `App.jsx` monolith risk and isolate page boundaries.

- [ ] Extract `Tasks` page from `App.jsx`.
- [ ] Continue routing/composition shell simplification.
- [ ] Add minimal page-level tests for extracted modules.

**Acceptance criteria:**
- `App.jsx` is primarily routing/composition.
- Behavior parity maintained for dashboard/candidates/tasks/detail flows.

---

### Phase P5 — Landing page message + brand-agnostic readiness (Owner: Agent D)

**Goal:** improve sales clarity and de-risk rebrand.

- [ ] Add explicit “What we test (30+ signals)” section on front page.
- [ ] Clarify core value proposition with concrete examples.
- [ ] Centralize brand name/domain/assets into config/constants.
- [ ] Ensure email templates/page titles/logo references use centralized brand config.

**Acceptance criteria:**
- Landing page clearly explains what is assessed.
- Brand rename can be done in one configuration surface.

---

### Phase P6 — Model-tier strategy + cost observability (Owner: Agent E)

**Goal:** keep costs controlled while preserving quality path.

- [ ] Set cheapest Claude model as default for test/staging.
- [ ] Keep production model configurable via environment.
- [ ] Track per-assessment and per-tenant costs across Claude/E2B/email/storage.
- [ ] Add cost dashboard metrics + thresholds (daily spend, cost/completed assessment).

**Acceptance criteria:**
- Model tiering is environment-driven and documented.
- Costs attributable by tenant and assessment.

---

### Phase P7 — Integration, QA, and release gate (Owner: Agent F)

**Goal:** merge all phase outputs safely with measurable release confidence.

- [ ] Run full local-safe QA matrix and production build.
- [ ] Validate no doc drift across `README`, `PRODUCT_PLAN.md`, and `RALPH_TASK.md`.
- [ ] Publish release notes mapping each phase to shipped outcomes.

**Validation commands:**
- `cd backend && pytest -q -m "not production"`
- `cd frontend && npm test -- --run`
- `cd frontend && npm run build`

**Release gate:**
- [ ] All phase acceptance criteria met.
- [ ] No blocking regressions in assessment runtime, scoring, or candidate comparison.

---

### Suggested execution order for multiple agents

1. **P0 (F)** baseline lock
2. **Parallel:** **P1 (A)** + **P2 (B)** + **P5 (D)**
3. **Then:** **P3 (C)** (depends on P2 glossary/score contracts)
4. **Then:** **P4 (C)** decomposition cleanup
5. **Parallel:** **P6 (E)** + integration prep by **F**
6. **Final:** **P7 (F)** release gate

---

## 5) Backlog (non-blocking)

- [ ] Candidate comparison views with overlay + side-by-side mode.
- [ ] Scoring dimension glossary + chart tooltip system.
- [ ] Centralized brand configuration (name/domain/assets) for easy rebrand.
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

